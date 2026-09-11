"""只读执行简历样本的解析、结构化与初筛核心回归诊断。

本脚本直接复用生产解析器、结构化函数和初筛纯评分入口：

1. 不创建 Candidate、Application、WorkflowRun 或任何数据库业务记录；
2. 仅只读加载一份已发布岗位画像和院校排名，以验证评分核心的输入完整性；
3. 不输出简历正文、模型提示词或原始回包，只输出阶段状态、数量和稳定错误码；
4. 适用于本地人工回归。正式自动化测试仍应使用脱敏/合成夹具，不提交真实简历。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from sqlalchemy import select

from backend.app.bootstrap.model_gateway import configure_model_infrastructure
from backend.app.db.session import SessionLocal
from backend.app.models.entities import JobRequirementProfileRecord, UniversityRankingEntry
from backend.app.modules.document_ingestion.parsing.parser import DocumentParsingPipeline
from recruitment_ai_core.resume_structuring.pipeline import structure_resume
from recruitment_ai_core.resume_structuring.pipeline import resume_ir_from_structure
from recruitment_ai_core.screening_scoring import run_screening_scoring
from recruitment_ai_core.screening_scoring.result_contracts import ScoringCoreInput
from recruitment_ai_core.screening_scoring.work_unit_structurer import build_resume_profile


def _safe_error(exc: Exception) -> str:
    """只保留异常类型和稳定错误码，避免把简历内容写入诊断输出。"""
    return f"{type(exc).__name__}:{str(exc)[:300]}"


def _validation_codes(structure: dict[str, Any]) -> list[str]:
    """提取结构校验码，供 PowerShell 输出和后续回归断言使用。"""
    return [
        str(item.get("code") or "")
        for item in (structure.get("validation") or {}).get("errors") or []
        if isinstance(item, dict) and item.get("code")
    ]


def _load_scoring_fixture() -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    """只读读取一份可用岗位画像和完整院校排名，复用生产评分合同。"""
    with SessionLocal() as db:
        profiles = list(
            db.scalars(
                select(JobRequirementProfileRecord).order_by(
                    JobRequirementProfileRecord.created_at.desc()
                )
            )
        )
        profile = next(
            (
                item
                for item in profiles
                if (item.payload or {}).get("job_profile_version_id")
                and (item.payload or {}).get("preset_model_id")
                and (item.payload or {}).get("preset_model_version")
            ),
            None,
        )
        if profile is None:
            raise RuntimeError("diagnostic_job_profile_missing")
        version = db.scalar(
            select(UniversityRankingEntry.dataset_version)
            .order_by(UniversityRankingEntry.dataset_version.desc())
            .limit(1)
        )
        if not version:
            raise RuntimeError("diagnostic_ranking_dataset_missing")
        rankings = [
            {
                "canonical_name": row.canonical_name,
                "aliases": list((row.aliases or {}).get("items", [])),
                "rank": row.rank,
            }
            for row in db.scalars(
                select(UniversityRankingEntry)
                .where(UniversityRankingEntry.dataset_version == version)
                .order_by(UniversityRankingEntry.rank)
            )
        ]
    return dict(profile.payload or {}), rankings, str(version)


def _diagnose_one(
    path: Path,
    *,
    parser: DocumentParsingPipeline,
    job_profile: dict[str, Any],
    rankings: list[dict[str, Any]],
    ranking_version: str,
) -> dict[str, Any]:
    """顺序执行生产解析、结构化和纯评分；任一阶段失败都停止后续阶段。"""
    result: dict[str, Any] = {
        "file": path.name,
        "parse": {"status": "not_started"},
        "structure": {"status": "not_started"},
        "scoring": {"status": "not_started"},
    }
    started_at = time.monotonic()
    try:
        parsed = parser.parse(
            path.read_bytes(),
            path.name,
            data_id=f"resume-regression-{path.stem[:24]}",
            timeout_seconds=300,
            idempotency_key=f"resume-regression-{path.name}",
        )
        metadata = dict(parsed.metadata or {})
        result["parse"] = {
            "status": "passed",
            "provider": str(metadata.get("provider") or ""),
            "pageCount": int(metadata.get("page_count") or 0),
            "blockCount": len(parsed.blocks or []),
            "textCharacters": len(parsed.text or ""),
            "qualityScore": metadata.get("quality_score"),
        }
    except Exception as exc:
        result["parse"] = {"status": "failed", "error": _safe_error(exc)}
        result["totalDurationMs"] = round((time.monotonic() - started_at) * 1000, 1)
        return result

    try:
        structure = structure_resume(
            candidate_id=f"REGRESSION_{path.stem[:20]}",
            resume_text=parsed.text,
            document_blocks=list(parsed.blocks or []),
        )
        resume_ir = dict(structure.get("resume_ir") or {})
        structure_status = str(structure.get("status") or "failed")
        result["structure"] = {
            "status": structure_status,
            "method": str(structure.get("method") or ""),
            "failureReason": str(structure.get("failure_reason") or ""),
            "validationCodes": _validation_codes(structure),
            "experienceUnitCount": len(resume_ir.get("experience_units") or []),
            "sourceBulletCount": len(resume_ir.get("source_bullets") or []),
            "scorableWorkUnitCount": len(resume_ir.get("scorable_work_units") or []),
        }
        if structure_status not in {"passed", "repaired"}:
            result["scoring"] = {
                "status": "blocked",
                "reason": "structure_not_publishable",
            }
            result["totalDurationMs"] = round((time.monotonic() - started_at) * 1000, 1)
            return result
    except Exception as exc:
        result["structure"] = {"status": "failed", "error": _safe_error(exc)}
        result["scoring"] = {"status": "blocked", "reason": "structure_exception"}
        result["totalDurationMs"] = round((time.monotonic() - started_at) * 1000, 1)
        return result

    try:
        profile = build_resume_profile(
            resume_ir_from_structure(structure, candidate_id=f"REGRESSION_{path.stem[:20]}")
        )
        core = run_screening_scoring(
            ScoringCoreInput(
                resume_profile=profile,
                job_profile=job_profile,
                education_ranking_entries=rankings,
                ranking_dataset_version=ranking_version,
            )
        )
        score = dict(core.score_result or {})
        result["scoring"] = {
            "status": "passed",
            "total": score.get("total", score.get("base_score")),
            "evidenceCount": len((core.evidence_index or {}).get("evidence") or []),
        }
    except Exception as exc:
        result["scoring"] = {"status": "failed", "error": _safe_error(exc)}
    result["totalDurationMs"] = round((time.monotonic() - started_at) * 1000, 1)
    return result


def main() -> int:
    # 命令行脚本不经过 FastAPI 的模块初始化；显式接入与应用相同的 LLM 网关，
    # 才能验证真实结构化与评分调用，而不是误报未配置网关。
    configure_model_infrastructure()
    parser = argparse.ArgumentParser(description="生产简历链路只读回归诊断")
    parser.add_argument("paths", nargs="+", type=Path, help="待诊断的 PDF 文件")
    args = parser.parse_args()
    job_profile, rankings, ranking_version = _load_scoring_fixture()
    document_parser = DocumentParsingPipeline()
    reports: list[dict[str, Any]] = []
    for path in args.paths:
        print(json.dumps({"event": "sample_started", "file": path.name}, ensure_ascii=False), flush=True)
        reports.append(
            _diagnose_one(
                path,
                parser=document_parser,
                job_profile=job_profile,
                rankings=rankings,
                ranking_version=ranking_version,
            )
        )
        print(json.dumps(reports[-1], ensure_ascii=False), flush=True)
    print(json.dumps({"event": "summary", "samples": reports}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
