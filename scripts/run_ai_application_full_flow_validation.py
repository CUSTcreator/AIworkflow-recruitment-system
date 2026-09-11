from __future__ import annotations

import argparse
from io import BytesIO
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATASET_DIR = Path(r"D:\For studying-or-working\Develop-Project\简历数据集")
JD_PATH = Path(r"D:\For studying-or-working\Develop-Project\对应JD\ai应用开发.txt")
RESUME_PATHS = [
    DATASET_DIR / f"ai应用开发{index:02d}.txt" for index in range(1, 6)
]


SCENARIOS: dict[str, dict[str, Any]] = {
    "01": {
        "first": [
            "候选人能够逐步说明企业知识库中缓存、BM25、向量召回、RRF、父子分块和重排序的调用顺序，并说明由本人完成检索链路与评测流程。",
            "候选人说明使用固定问题集比较检索策略，关注答案忠实度、相关性、上下文精确率和召回率，能够解释指标变化如何用于调整分块和 Prompt。",
            "候选人能够分析多 Agent 工具超时、重复调用和错误结果传播，提出调用上限、指数退避、幂等键、降级路径和链路追踪方案。",
            "需要限定的是，简历中的评测结果来自自建离线测试集，不是线上 A/B 实验；尚未经历大规模 C 端真实流量验证。",
        ],
        "second": [
            "候选人选择 AI 应用方向的动机明确，能够把模型能力、产品目标和工程约束一起讨论。",
            "候选人举例说明需求不清时先确认用户目标、成功指标和不可接受结果，再拆分原型验证。",
            "候选人对跨角色协作的描述具体，能说明接口约定、异常边界和进度同步方式。",
            "对 C 端业务经验仍有限，但愿意通过埋点、反馈闭环和灰度实验补足。",
        ],
    },
    "02": {
        "first": [
            "候选人能够说明混合检索、Cross-Encoder 重排、缓存和监控链路，并能解释各组件解决的问题。",
            "现场给出检索质量下降的排查顺序：先固定测试集，再区分解析、召回、排序和生成阶段，最后对照指标定位。",
            "候选人确认百万级向量和五千次压测主要来自个人项目的构造数据；三百毫秒指标是缓存或检索接口耗时，不包含完整大模型生成时间。",
            "编码思路完整，能够处理超时和重试，但对并发写入下的幂等、补偿和数据一致性说明不够深入。",
        ],
        "second": [
            "候选人希望继续从事 RAG 和 Agent 工程，岗位方向匹配。",
            "能够说明个人项目中如何拆分任务和验证结果，但团队协作案例主要来自短期实习。",
            "面对产品目标变化时会先确认评价指标，再调整检索和交互方案。",
            "能够接受代码评审和导师反馈，对指标口径也愿意进一步校准。",
        ],
    },
    "03": {
        "first": [
            "候选人能够说明 Agentic RAG 中查询改写、混合检索、补充检索、缓存和链路追踪的基本流程。",
            "关于幻觉率从百分之十七点四降到零，候选人说明这是小规模离线样本结果，样本构造、对照组和重复实验还不充分。",
            "现场系统设计能够给出超时重试和 BM25 降级，但未完整处理幂等、并发状态、失败补偿和评测回归。",
            "候选人目前主要是单个个人项目，尚缺少真实用户需求迭代、生产故障处理和跨团队交付证据。",
        ],
        "second": [
            "候选人对 AI 应用有兴趣，能够说明近期学习计划。",
            "岗位理解主要集中在技术实现，对 C 端场景、用户反馈和产品指标考虑较少。",
            "协作经历较少，能够接受导师拆解任务，但独立推进复杂需求的证据不足。",
            "候选人愿意补充评测方法和工程基础，当前仍需要较多指导。",
        ],
    },
    "04": {
        "first": [
            "候选人能够清楚区分实习中的行业数据采集、清洗入库、向量同步、Agent 查询和业务 API，并说明本人负责的数据管道及查询接口边界。",
            "候选人结合真实业务说明数据质量、增量更新、重复数据、失败重跑和监控告警的处理方法，能够给出可落地的幂等与恢复方案。",
            "对 RAG 项目能够解释多模态解析、元数据设计、Dense 与 Sparse 召回、RRF、重排和动态 Top-K 的取舍，并能根据失败样本提出评测集。",
            "候选人说明效率提升和接口耗时来自业务日志口径，能够主动区分检索接口、缓存命中和完整生成链路，指标边界清楚。",
        ],
        "second": [
            "候选人能从业务人员竞品追踪场景说明为什么采用知识库和 Agent，而不是只介绍技术栈。",
            "需求变化时会先确认使用者、数据时效、报告口径和验收指标，再安排增量交付。",
            "候选人能够举出与运营及业务小组对齐字段、接口和异常反馈的具体协作过程。",
            "岗位动机和 AI 应用工程方向一致，对后续评测、可观测和成本优化有明确计划。",
        ],
    },
    "05": {
        "first": [
            "候选人能够说明旅行 Agent 的任务拆分、工具路由、MCP 接入、重试降级和 SSE 输出等基本设计。",
            "关于多 Agent 效率提升百分之六十，候选人说明来自本地少量任务的执行时间对比，没有固定数据集和多轮重复实验。",
            "面对第三方工具重复执行场景，候选人提出重试和保底结果，但对幂等键、状态持久化、补偿事务和并发冲突说明较弱。",
            "Agentic RAG 项目可以说明本地知识库与联网搜索切换，但缺少检索质量指标、失败样本分类和持续评测闭环。",
        ],
        "second": [
            "候选人对 Agent 开发兴趣明确，能够描述想继续深入的技术方向。",
            "产品场景思考偏技术方案，对用户验证、成功指标和上线后的反馈闭环描述较少。",
            "能够接受任务拆解和代码评审，但团队协作实例不够具体。",
            "候选人愿意补足测试和评测体系，当前更适合边界清晰的开发任务。",
        ],
    },
}

SPARSE_SCENARIOS: dict[str, dict[str, Any]] = {
    "ai03_sparse": {
        "first": [
            "候选人能够结合RAG项目清楚说明查询改写、混合检索、重排序和失败样本分析流程，并能解释Top-K调整依据。",
            "候选人无法解释项目中幂等键的生成和并发重复请求处理，确认相关实现主要参考现成示例，未进行并发验证。",
        ],
        "first_free_only": True,
        "second": [
            "候选人愿意继续从事AI应用开发，可两周内到岗，期望薪资在岗位预算范围内。",
        ],
    }
}


def _prepare_environment(output_dir: Path) -> None:
    os.environ["DATABASE_URL"] = (
        f"sqlite:///{(output_dir / 'full_flow_validation.db').resolve().as_posix()}"
    )
    os.environ["LOCAL_OBJECT_STORE_DIR"] = str(
        (output_dir / "object_store").resolve()
    )
    os.environ["STORAGE_BACKEND"] = "local"
    os.environ["ALLOW_STORAGE_FALLBACK"] = "0"
    os.environ["WORKFLOW_EXECUTION_MODE"] = "queued"
    os.environ.setdefault("WORKFLOW_WORKER_LEASE_SECONDS", "900")
    llm_config_path = ROOT / "config" / "llm.local.json"
    if llm_config_path.exists():
        os.environ["RECRUIT_LLM_CONFIG_PATH"] = str(llm_config_path.resolve())
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    package_root = ROOT / "packages" / "recruitment_ai_core"
    if str(package_root) not in sys.path:
        sys.path.insert(0, str(package_root))


def _progress(output_dir: Path, message: str) -> None:
    with (output_dir / "progress.log").open("a", encoding="utf-8") as handle:
        handle.write(message + "\n")
    try:
        print(message, flush=True)
    except OSError:
        pass


def _headers(token: str, key: str | None = None) -> dict[str, str]:
    result = {"Authorization": f"Bearer {token}"}
    if key:
        result["Idempotency-Key"] = key
    return result


def _pdf_bytes(text: str) -> bytes:
    import fitz

    document = fitz.open()
    lines = [line for line in text.splitlines() if line.strip()]
    for start in range(0, max(1, len(lines)), 42):
        page = document.new_page()
        page.insert_textbox(
            fitz.Rect(48, 48, page.rect.width - 48, page.rect.height - 48),
            "\n".join(lines[start : start + 42]),
            fontname="china-s",
            fontsize=9,
            lineheight=1.35,
        )
    payload = document.tobytes()
    document.close()
    return payload


def _job_xlsx_bytes(jd_text: str) -> bytes:
    from openpyxl import Workbook

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "招聘计划"
    worksheet.append(
        ["序号", "部门", "职位名称", "招聘人数", "工作职责", "任职资格", "学历要求"]
    )
    worksheet.append(
        [1, "技术部", "AI应用开发工程师", 1, jd_text, jd_text, "本科及以上"]
    )
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()

def _post(
    client: Any,
    token: str,
    path: str,
    *,
    key: str,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = client.post(
        path,
        headers=_headers(token, key),
        json=body or {},
    )
    if response.status_code >= 400:
        raise RuntimeError(f"{path}:{response.status_code}:{response.text[:800]}")
    return dict(response.json())


def _run_queued(
    client: Any,
    token: str,
    path: str,
    *,
    key: str,
    worker_id: str,
    body: dict[str, Any] | None = None,
) -> str:
    from backend.app.db.session import SessionLocal
    from backend.app.models.entities import WorkflowRun
    from backend.app.workers.workflow_worker import process_next_workflow_run

    accepted = _post(client, token, path, key=key, body=body)
    run_id = str(accepted["workflow_run_id"])
    if not process_next_workflow_run(worker_id):
        raise RuntimeError(f"worker_did_not_claim:{run_id}")
    with SessionLocal() as db:
        run = db.get(WorkflowRun, run_id)
        if run is None or run.status != "completed":
            raise RuntimeError(
                f"workflow_failed:{run_id}:{run.status if run else 'missing'}:"
                f"{run.error_message if run else ''}"
            )
    return run_id


def _latest_payload(db: Any, model: Any, application_id: str) -> dict[str, Any]:
    from sqlalchemy import select

    row = db.scalars(
        select(model)
        .where(model.application_id == application_id)
        .order_by(model.created_at.desc())
    ).first()
    return dict(row.payload or {}) if row else {}


def _latest_guide(db: Any, application_id: str, round_value: str) -> dict[str, Any]:
    from backend.app.models.entities import InterviewGuide
    from sqlalchemy import select

    rows = db.scalars(
        select(InterviewGuide)
        .where(InterviewGuide.application_id == application_id)
        .order_by(InterviewGuide.created_at.desc())
    ).all()
    for row in rows:
        if row.payload.get("round") == round_value:
            return dict(row.payload)
    return {}


def _question_responses(
    guide: dict[str, Any],
    statements: list[str],
    *,
    round_value: str,
) -> list[dict[str, Any]]:
    questions = list(guide.get("questions") or [])
    if not questions:
        questions = [
            {"questionId": f"Q_{round_value.upper()}_{index:03d}"}
            for index in range(1, len(statements) + 1)
        ]
    result = []
    for index, question in enumerate(questions[: len(statements)]):
        question_id = (
            question.get("questionId")
            or question.get("question_id")
            or f"Q_{round_value.upper()}_{index + 1:03d}"
        )
        result.append(
            {
                "questionId": question_id,
                "answerSummary": statements[index],
                "interviewerNote": "模拟结构化面评：记录补充说明。",
                "confirmedByInterviewer": True,
            }
        )
    return result


def _score_summary(payload: dict[str, Any]) -> dict[str, Any]:
    score = float(
        payload.get("final_decision_score")
        or payload.get("final_review_score")
        or payload.get("base_score")
        or payload.get("baseScore")
        or payload.get("score")
        or 0
    )
    return {
        "total": round(score, 2),
        "capability_total": _number(
            payload,
            "capability_total_score",
            "role_capability_score",
            "base_score",
            "baseScore",
        ),
        "decision_fit": _number(payload, "decision_fit_score"),
        "job": _number(
            payload,
            "job_capability_fit_score",
            "jobCapabilityFitScore",
            "job_requirement_score",
        ),
        "resume": _number(
            payload,
            "resume_experience_score",
            "resumeExperienceScore",
            "resume_demonstrated_capability_score",
        ),
        "education": _number(
            payload,
            "education_background_score",
            "educationBackgroundScore",
        ),
        "confidence": payload.get("confidence"),
    }


def _number(payload: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = payload.get(key)
        if value is not None:
            return round(float(value), 2)
    return None


def _call_count(value: Any) -> int:
    ids: set[str] = set()

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            call_id = item.get("call_id")
            if isinstance(call_id, str) and call_id:
                ids.add(call_id)
            for nested in item.values():
                visit(nested)
        elif isinstance(item, list):
            for nested in item:
                visit(nested)

    visit(value)
    return len(ids)


def _collect_result(application_id: str, workflow_ids: list[str]) -> dict[str, Any]:
    from backend.app.db.session import SessionLocal
    from backend.app.models.entities import (
        Application,
        CandidateCapabilityProfileRecord,

        InterviewParseResultRecord,
        InterviewRecordRecord,
        ScoreSnapshot,
        StageHistory,
        WorkflowArtifact,
        WorkflowRun,
    )
    from sqlalchemy import select

    with SessionLocal() as db:
        app = db.get(Application, application_id)
        snapshots = db.scalars(
            select(ScoreSnapshot)
            .where(ScoreSnapshot.application_id == application_id)
            .order_by(ScoreSnapshot.created_at)
        ).all()
        by_stage = {row.stage: _score_summary(row.payload) for row in snapshots}
        profiles = db.scalars(
            select(CandidateCapabilityProfileRecord)
            .where(CandidateCapabilityProfileRecord.application_id == application_id)
            .order_by(CandidateCapabilityProfileRecord.version)
        ).all()

        parse_results = db.scalars(
            select(InterviewParseResultRecord)
            .where(InterviewParseResultRecord.application_id == application_id)
            .order_by(InterviewParseResultRecord.version)
        ).all()
        records = db.scalars(
            select(InterviewRecordRecord).where(
                InterviewRecordRecord.application_id == application_id
            )
        ).all()
        record_ids = {row.record_id for row in records}
        referenced_ids = {
            source_id
            for parse_result in parse_results
            for source_id in parse_result.payload.get("source_record_ids", [])
        }
        artifacts = db.scalars(
            select(WorkflowArtifact).where(
                WorkflowArtifact.application_id == application_id
            )
        ).all()
        artifact_payload = {
            f"{row.artifact_type}:{row.workflow_run_id}": row.artifact_json
            for row in artifacts
        }
        post_first = next(
            (
                row.artifact_json
                for row in reversed(artifacts)
                if row.artifact_type == "post_first_scoring_bundle"
            ),
            {},
        )
        post_second = next(
            (
                row.artifact_json
                for row in reversed(artifacts)
                if row.artifact_type == "post_second_scoring_bundle"
            ),
            {},
        )
        runs = [db.get(WorkflowRun, run_id) for run_id in workflow_ids]
        stages = db.scalars(
            select(StageHistory)
            .where(StageHistory.application_id == application_id)
            .order_by(StageHistory.created_at)
        ).all()
        v1 = by_stage.get("screening", {"total": 0})
        v2 = by_stage.get("after_first_interview", {"total": 0})
        v3 = by_stage.get("after_second_interview", {"total": 0})
        return {
            "application_id": application_id,
            "status": app.status if app else "missing",
            "v1": v1,
            "v2": v2,
            "v3": v3,
            "delta_first": round(v2["total"] - v1["total"], 2),
            "delta_second": round(v3["total"] - v2["total"], 2),
            "profile_versions": [
                {
                    "version": row.version,
                    "stage": row.stage,
                    "profile_id": row.profile_id,
                }
                for row in profiles
            ],
            "interview_parse_results": [
                {
                    "stage": row.stage,
                    "experience_update_count": len(
                        row.payload.get("experience_updates", [])
                    ),
                    "observation_count": len(
                        row.payload.get("interview_observations", [])
                    ),
                    "non_scoring_count": len(row.payload.get("non_scoring", [])),
                    "source_record_ids": row.payload.get("source_record_ids", []),
                }
                for row in parse_results
            ],
            "first_changes": {
                "assessment": len(post_first.get("assessment_changes", [])),
                "risk": len(post_first.get("risk_changes", [])),
                "gap": len(post_first.get("gap_changes", [])),
            },
            "second_changes": {
                "assessment": len(post_second.get("assessment_changes", [])),
                "risk": len(post_second.get("risk_changes", [])),
                "gap": len(post_second.get("gap_changes", [])),
            },
            "interview_record_count": len(records),
            "source_lineage_valid": referenced_ids <= record_ids,
            "missing_source_record_ids": sorted(referenced_ids - record_ids),
            "workflow_runs": [
                {
                    "workflow_type": row.workflow_type,
                    "status": row.status,
                    "attempt_count": row.attempt_count,
                }
                for row in runs
                if row is not None
            ],
            "stage_path": [f"{row.from_status}->{row.to_status}" for row in stages],
            "llm_call_count": _call_count(artifact_payload),
            "artifacts": artifact_payload,
        }


def _run_candidate(
    client: Any,
    *,
    application_id: str,
    suffix: str,
    hr_token: str,
    manager_token: str,
    initial_scoring_run_id: str,
    scenario_key: str | None = None,
) -> dict[str, Any]:
    from backend.app.db.session import SessionLocal
    from backend.app.models.entities import WorkflowRun
    from backend.app.workers.workflow_worker import process_next_workflow_run

    scenario = (
        SPARSE_SCENARIOS[scenario_key]
        if scenario_key
        else SCENARIOS[suffix]
    )
    worker_id = f"ai-full-worker-{suffix}"
    run_ids: list[str] = [initial_scoring_run_id]
    if not process_next_workflow_run(worker_id):
        raise RuntimeError(
            f"worker_did_not_claim:{initial_scoring_run_id}"
        )
    with SessionLocal() as db:
        initial_run = db.get(WorkflowRun, initial_scoring_run_id)
        if initial_run is None or initial_run.status != "completed":
            raise RuntimeError(
                f"workflow_failed:{initial_scoring_run_id}:"
                f"{initial_run.status if initial_run else 'missing'}:"
                f"{initial_run.error_message if initial_run else ''}"
            )
    _post(
        client,
        manager_token,
        f"/api/v1/applications/{application_id}/actions/approve-first-interview",
        key=f"{application_id}-approve-first",
    )
    run_ids.append(
        _run_queued(
            client,
            manager_token,
            f"/api/v1/applications/{application_id}/workflows/first-interview-planning/run",
            key=f"{application_id}-plan-first",
            worker_id=worker_id,
        )
    )
    with SessionLocal() as db:
        first_guide = _latest_guide(db, application_id, "first")
    _post(
        client,
        manager_token,
        f"/api/v1/applications/{application_id}/workflows/first-interview-planning/confirm",
        key=f"{application_id}-confirm-first",
        body={"plan": first_guide},
    )
    _post(
        client,
        manager_token,
        f"/api/v1/applications/{application_id}/actions/start-first-interview",
        key=f"{application_id}-start-first",
    )
    first_feedback = {
        "rawNotes": "\n".join(scenario["first"]),
        "questionResponses": (
            []
            if scenario.get("first_free_only")
            else _question_responses(
                first_guide,
                scenario["first"],
                round_value="first",
            )
        ),
        "assessmentSummary": "模拟一面：已核验项目事实、工程深度、评测方法和能力边界。",
        "overallRecommendation": "进入二面，继续验证产品场景和协作方式。",
    }
    _post(
        client,
        manager_token,
        f"/api/v1/applications/{application_id}/actions/finish-first-interview",
        key=f"{application_id}-finish-first",
        body=first_feedback,
    )
    run_ids.append(
        _run_queued(
            client,
            manager_token,
            f"/api/v1/applications/{application_id}/actions/submit-first-feedback",
            key=f"{application_id}-score-first",
            worker_id=worker_id,
            body=first_feedback,
        )
    )
    _post(
        client,
        hr_token,
        f"/api/v1/applications/{application_id}/actions/approve-second-interview",
        key=f"{application_id}-approve-second",
    )
    second_feedback = {
        "rawNotes": "\n".join(scenario["second"]),
        "questionResponses": [],
        "assessmentSummary": "模拟二面：已核验岗位动机、产品意识、协作方式和剩余风险。",
        "overallRecommendation": "提交最终人工审核。",
    }
    _post(
        client,
        hr_token,
        f"/api/v1/applications/{application_id}/actions/finish-second-interview",
        key=f"{application_id}-finish-second",
        body=second_feedback,
    )
    run_ids.append(
        _run_queued(
            client,
            hr_token,
            f"/api/v1/applications/{application_id}/actions/submit-second-feedback",
            key=f"{application_id}-score-second",
            worker_id=worker_id,
            body=second_feedback,
        )
    )
    _post(
        client,
        hr_token,
        f"/api/v1/applications/{application_id}/actions/final-decision",
        key=f"{application_id}-final",
        body={"decision": "manual_review"},
    )
    return _collect_result(application_id, run_ids)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--jd-path")
    parser.add_argument("--resume-path", action="append", dest="resume_paths")
    parser.add_argument(
        "--scenario-key",
        choices=sorted(SPARSE_SCENARIOS),
    )
    args = parser.parse_args()
    jd_path = Path(args.jd_path) if args.jd_path else JD_PATH
    resume_paths = (
        [Path(value) for value in args.resume_paths]
        if args.resume_paths
        else RESUME_PATHS
    )
    output_dir = Path(
        args.output_dir
        or ROOT
        / "validation_results"
        / f"ai_application_full_flow_{datetime.now():%Y%m%d_%H%M%S}"
    )
    output_dir.mkdir(parents=True, exist_ok=False)
    _prepare_environment(output_dir)

    from fastapi.testclient import TestClient
    from backend.app.db.session import SessionLocal
    from backend.app.main import app
    from backend.app.models.entities import WorkflowRun
    from backend.app.seeds.import_university_rankings import import_university_rankings
    from backend.app.workers.workflow_worker import process_next_workflow_run
    from sqlalchemy import select

    if not jd_path.is_file() or not all(path.is_file() for path in resume_paths):
        raise FileNotFoundError("AI应用JD或5份简历不完整")

    started = time.perf_counter()
    results: list[dict[str, Any]] = []
    with TestClient(app) as client:
        with SessionLocal() as db:
            import_university_rankings(db)
        hr_login = client.post(
            "/api/v1/auth/login",
            json={"username": "hr", "password": "Hr@123456"},
        )
        manager_login = client.post(
            "/api/v1/auth/login",
            json={
                "username": "department_manager",
                "password": "Dept@123456",
            },
        )
        hr_login.raise_for_status()
        manager_login.raise_for_status()
        hr_token = hr_login.json()["accessToken"]
        manager_token = manager_login.json()["accessToken"]
        jd_text = jd_path.read_text(encoding="utf-8")

        job_upload = client.post(
            "/api/v1/job-documents",
            headers=_headers(hr_token),
            files={
                "file": (
                    "job-requirements.xlsx",
                    _job_xlsx_bytes(jd_text),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
        job_upload.raise_for_status()
        job_document_id = job_upload.json()["document_id"]
        if not process_next_workflow_run("ai-full-job-import-worker"):
            raise RuntimeError("job_import_worker_did_not_claim")
        drafts_response = client.get(
            f"/api/v1/job-documents/{job_document_id}/drafts",
            headers=_headers(hr_token),
        )
        drafts_response.raise_for_status()
        drafts = drafts_response.json()
        if not drafts:
            raise RuntimeError("job_import_created_no_draft")
        draft = drafts[0]
        resolved = client.patch(
            f"/api/v1/job-documents/{job_document_id}/drafts/{draft['job_draft_id']}",
            headers=_headers(hr_token),
            json={"department_id": "DEPT_TECH"},
        )
        resolved.raise_for_status()
        confirmed = client.post(
            f"/api/v1/job-documents/{job_document_id}/confirm",
            headers=_headers(hr_token),
            json={"draft_ids": [draft["job_draft_id"]]},
        )
        confirmed.raise_for_status()
        job_id = confirmed.json()["jobs"][0]["job_id"]

        imported: list[tuple[str, str, str, str]] = []
        for index, resume_path in enumerate(resume_paths[: args.limit], start=1):
            suffix = f"{index:02d}"
            upload = client.post(
                "/api/v1/resume-documents",
                headers=_headers(hr_token),
                data={
                    "job_id": job_id,
                    "candidate_name": resume_path.stem,
                    "external_candidate_id": f"AI_CAND_FULL_{suffix}",
                    "external_application_id": f"AI_APP_FULL_{suffix}",
                },
                files={
                    "file": (
                        f"{resume_path.stem}.pdf",
                        _pdf_bytes(resume_path.read_text(encoding="utf-8")),
                        "application/pdf",
                    )
                },
            )
            upload.raise_for_status()
            submission_id = upload.json()["submission_id"]
            if not process_next_workflow_run(
                f"ai-full-resume-import-worker-{suffix}"
            ):
                raise RuntimeError(
                    f"resume_import_worker_did_not_claim:{submission_id}"
                )
            submission = client.get(
                f"/api/v1/resume-documents/{submission_id}",
                headers=_headers(hr_token),
            )
            submission.raise_for_status()
            application_id = submission.json()["application_id"]
            if not application_id:
                raise RuntimeError(
                    f"resume_import_created_no_application:{submission_id}"
                )
            initial_scoring_run_id: str | None = None
            for foundation_step in range(4):
                with SessionLocal() as db:
                    scoring_run = db.scalar(
                        select(WorkflowRun)
                        .where(
                            WorkflowRun.application_id == application_id,
                            WorkflowRun.workflow_type == "scoring_workflow",
                            WorkflowRun.status == "pending",
                        )
                        .order_by(WorkflowRun.started_at)
                    )
                    if scoring_run is not None:
                        initial_scoring_run_id = scoring_run.workflow_run_id
                        break
                if not process_next_workflow_run(
                    f"ai-full-foundation-worker-{suffix}-{foundation_step}"
                ):
                    break
            if initial_scoring_run_id is None:
                raise RuntimeError(
                    f"initial_scoring_run_missing:{application_id}"
                )
            imported.append(
                (
                    application_id,
                    suffix,
                    resume_path.stem,
                    initial_scoring_run_id,
                )
            )

        for case_index, (
            application_id,
            suffix,
            name,
            initial_scoring_run_id,
        ) in enumerate(imported, start=1):
            case_started = time.perf_counter()
            _progress(output_dir, f"START {case_index}/{len(imported)} {name}")
            try:
                result = _run_candidate(
                    client,
                    application_id=application_id,
                    suffix=suffix,
                    hr_token=hr_token,
                    manager_token=manager_token,
                    initial_scoring_run_id=initial_scoring_run_id,
                    scenario_key=args.scenario_key,
                )
                result["candidate"] = name
                result["simulation"] = True
                result["elapsed_seconds"] = round(
                    time.perf_counter() - case_started, 2
                )
            except Exception as exc:
                result = {
                    "application_id": application_id,
                    "candidate": name,
                    "status": "failed",
                    "simulation": True,
                    "error": f"{type(exc).__name__}:{str(exc)[:1200]}",
                    "elapsed_seconds": round(
                        time.perf_counter() - case_started, 2
                    ),
                }
            results.append(result)
            (output_dir / f"{application_id}.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            (output_dir / "summary.json").write_text(
                json.dumps(results, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            _progress(
                output_dir,
                "RESULT "
                + json.dumps(
                    {
                        key: result.get(key)
                        for key in (
                            "candidate",
                            "status",
                            "v1",
                            "v2",
                            "v3",
                            "delta_first",
                            "delta_second",
                            "source_lineage_valid",
                            "elapsed_seconds",
                            "error",
                        )
                    },
                    ensure_ascii=False,
                ),
            )

    manifest = {
        "simulation": True,
        "notice": "V1来自导入的简历与JD；V2/V3使用模拟面评，仅用于完整链路验证。",
        "candidate_count": len(results),
        "completed_count": sum(item.get("status") == "manual_review" for item in results),
        "elapsed_seconds": round(time.perf_counter() - started, 2),
        "sources": [
            "https://www.opm.gov/policy-data-oversight/assessment-and-selection/structured-interviews",
            "https://cloud.google.com/blog/topics/developers-practitioners/master-generative-ai-evaluation-from-single-prompts-to-complex-agents",
            "https://learn.microsoft.com/en-us/security/zero-trust/sfi/observability-ai-systems",
        ],
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _progress(output_dir, f"OUTPUT_DIR {output_dir.resolve()}")
    return 0 if manifest["completed_count"] == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
