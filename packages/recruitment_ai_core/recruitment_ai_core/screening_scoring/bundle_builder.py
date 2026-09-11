from __future__ import annotations

import hashlib
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from .policy import BUNDLE_SCHEMA_VERSION
from .profile_evidence import nested_source_bullets, nested_work_units


def build_analysis_bundle(
    *,
    input_data: Any,
    resume_ir: Any,
    scored: dict[str, Any],
    qualification: dict[str, Any],
) -> dict[str, Any]:
    # 1. 读取本次评分各阶段已经生成的结果；分析包只汇总，不重新执行算法。
    job_profile = scored.get("job_profile", {})
    resume_profile = scored.get("resume_profile", {})
    candidate_profile = scored.get("candidate_capability_profile", {})
    score_engine_input = scored.get("score_engine_input", {})
    score_engine_output = scored.get("score_engine_output", {})
    job_capability_result = scored.get("job_capability_result", {})
    resume_experience = scored.get("resume_experience_assessment", {})
    # 2. 新评分流程直接读取已冻结 ResumeProfile；历史回放才会携带 ResumeIR。
    resume_hashes = {
        "resume_raw_sha256": resume_profile.get("resume_raw_sha256"),
        "resume_redacted_sha256": resume_profile.get("resume_redacted_sha256"),
    }
    resume_provenance = (
        resume_ir.structuring_provenance if resume_ir is not None
        else dict(resume_profile.get("structuring_provenance") or {})
    )
    resume_quality_warnings = (
        list(resume_ir.input_quality_report.warnings)
        if resume_ir is not None and resume_ir.input_quality_report is not None
        else []
    )
    # 3. 生成完整审计包，固定输入版本、事实对象、中间结果、模型轨迹和最终结果。
    return {
        "bundle_schema_version": BUNDLE_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).replace(tzinfo=None).isoformat(),
        "application_id": input_data.application_id,
        "candidate_id": input_data.candidate_id,
        "job_id": input_data.job_id,
        # 3. 记录 JD 与简历哈希，证明这份分析包对应的精确输入版本。
        "input_hashes": {
            "jd_sha256": hashlib.sha256(
                input_data.jd_text.encode("utf-8")
            ).hexdigest(),
            "resume_raw_sha256": resume_hashes["resume_raw_sha256"],
            "resume_redacted_sha256": resume_hashes["resume_redacted_sha256"],
        },
        # 4. 保存可复原的简历事实、岗位画像和评分所采用的指标目录。
        "verified_resume_ir": asdict(resume_ir) if resume_ir is not None else {},
        "job_profile": job_profile,
        "resume_profile": resume_profile,
        "capability_indicator_catalog_version": scored.get(
            "capability_indicator_catalog_version"
        ),
        "applicability_policy_version": scored.get(
            "applicability_policy_version"
        ),
        "capability_indicator_catalog": scored.get(
            "capability_indicator_catalog", {}
        ),
        # 5. 保存经历评估、岗位能力评估、能力画像和面试目标等主要算法产物。
        "resume_experience_assessment": resume_experience,
        "project_experience_assessments": resume_experience.get(
            "project_experience_assessments", []
        ),
        "preset_experience_result": candidate_profile.get("preset_experience_result", {}),
        "job_result": candidate_profile.get("job_result", {}),
        "job_capability_assessment": job_capability_result,
        "job_capability_replay_manifest": job_capability_result.get(
            "replay_manifest", {}
        ),
        "job_capability_prompt_traces": job_capability_result.get(
            "prompt_traces", []
        ),
        "interview_targets": list(scored.get("interview_targets", [])),
        "candidate_capability_profile": candidate_profile,
        # 6. 保存分数引擎输入输出；运行时评分证据可由固定输入和本包中的经历结果重建。
        "score_engine_input": score_engine_input,
        "score_engine_output": score_engine_output,
        "all_evidence_references": _all_evidence_references(resume_profile),
        # 7. 保存各阶段模型调用轨迹，不把模型输出当作未经验证的业务事实。
        "llm_trace": {
            "resume_structuring": resume_provenance,
            "resume_experience": resume_experience.get("llm_audit", {}),
            "job_capability": {
                "prompt_traces": job_capability_result.get("prompt_traces", [])
            },
        },
        # 8. 保存独立的硬筛资格结论及总分各分项。
        "qualification": qualification,
        # 9. 只在这里归档最终显示分；资格结论不会被合并进综合分。
        "final_scores": {
            "job_capability_fit_score": score_engine_output.get(
                "job_capability_fit_score"
            ),
            "resume_experience_score": score_engine_output.get(
                "resume_experience_score"
            ),
            "education_background_score": score_engine_output.get(
                "education_background_score"
            ),
            "base_score": score_engine_output.get("base_score"),
            "final_score": score_engine_output.get("score"),
        },
        # 10. 收集输入质量、经历判定和岗位匹配的警告，供后台排查或人工复核。
        "warnings": [
            *resume_quality_warnings,
            *resume_experience.get("validation_errors", []),
            *job_capability_result.get("warnings", []),
        ],
        "errors": [],
    }


def _all_evidence_references(
    resume_profile: dict[str, Any],
) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []
    for source_bullet in nested_source_bullets(resume_profile):
        references.append(
            {
                "evidence_id": source_bullet.get("source_bullet_id"),
                "evidence_type": "source_bullet",
                "raw_text": source_bullet.get("raw_text", ""),
                "source_line_start": source_bullet.get("source_line_start"),
                "source_line_end": source_bullet.get("source_line_end"),
                "source_block_ids": source_bullet.get(
                    "source_block_ids", []
                ),
                "work_unit_ids": source_bullet.get("work_unit_ids", []),
            }
        )
    for work_unit in nested_work_units(resume_profile):
        references.append(
            {
                "evidence_id": work_unit.get("work_unit_id"),
                "evidence_type": "scorable_work_unit",
                "raw_text": work_unit.get("raw_text", ""),
                "source_line_start": work_unit.get("source_line_start"),
                "source_line_end": work_unit.get("source_line_end"),
                "source_bullet_id": work_unit.get("source_bullet_id"),
            }
        )
    return references

