"""用隔离数据验证：普通面评能否真实影响 V2/V3 分数。

本脚本刻意不读取开发库中的 Candidate、Application 或简历，也不调用上传、PDF
解析流程。它使用 ``seed_app_001`` 创建一套虚构的 Agent 实习生数据，在独立 SQLite
数据库内依次运行 V1、题单、V2 与 V3。面评 LLM 仍按当前 ``llm.local.json`` 调用，
这样可同时检查“语义解析是否命中能力”与“增量评分是否真实产生分数变化”。

运行前必须由调用方设置 DATABASE_URL/LOCAL_OBJECT_STORE_DIR；不要直接在开发库执行。
输出只含版本、分数、命中数量与变化量，不打印面评原文或任何个人信息。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.db.init_db import init_db
from backend.app.db.session import Base, SessionLocal, engine
from backend.app.main import app
from backend.app.models.entities import ApplicationAssessmentVersion, InterviewParseResultRecord
from backend.app.workers.workflow_worker import process_next_workflow_run
from backend.tests.auth_helpers import auth_headers, post
from backend.tests.seed_app_001_fixture import seed_app_001


# 两段均为日常招聘中常见的 30～80 字面评。V2 用正向技术观察，V3 用负向追问观察，
# 以验证增量评分不仅能上调，也能按本轮证据下调受影响能力。
FIRST_NOTE = "候选人能清晰说明 RAG 召回链路、指标取舍和故障定位步骤，追问后仍能解释个人负责的接口调试与性能优化。"
SECOND_NOTE = "候选人无法说明离线评测指标和召回率计算，追问故障定位步骤时回答模糊，实际技术贡献范围与简历描述存在明显偏差。"


def _assert_processed(label: str) -> None:
    """同步消费一条已入队 WorkflowRun；失败时明确指出所属阶段。"""

    if not process_next_workflow_run("v2-v3-score-impact-test"):
        raise RuntimeError(f"workflow_not_processed:{label}")


def _snapshot(stage: str) -> dict[str, Any]:
    """只读取某一正式 AAV 与其 IPR，提取可比较的最小结果集。"""

    with SessionLocal() as db:
        assessment = db.scalars(
            select(ApplicationAssessmentVersion)
            .where(
                ApplicationAssessmentVersion.application_id == "APP_001",
                ApplicationAssessmentVersion.stage == stage,
            )
            .order_by(ApplicationAssessmentVersion.version.desc())
        ).first()
        if assessment is None:
            raise RuntimeError(f"assessment_missing:{stage}")
        core = dict(assessment.core_result_json or {})
        score = dict(core.get("score_result") or {})
        parse = (
            db.get(InterviewParseResultRecord, assessment.source_interview_parse_result_id)
            if assessment.source_interview_parse_result_id
            else None
        )
        parse_payload = dict(parse.payload or {}) if parse is not None else {}
        return {
            "assessmentVersionId": assessment.assessment_version_id,
            "score": {
                key: score.get(key)
                for key in ("total", "job_fit", "experience", "education")
            },
            "scoreChanges": list(core.get("score_changes") or []),
            "capabilityChanges": list(core.get("capability_changes") or []),
            "assertionCount": len(parse_payload.get("assertions") or []),
            "reviewRequired": bool(parse_payload.get("review_required")),
        }


def _delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, float | None]:
    """返回页面可展示的 V1→V2、V2→V3 总分与岗位匹配分差值。"""

    result: dict[str, float | None] = {}
    for key in ("total", "job_fit", "experience", "education"):
        left, right = before["score"].get(key), after["score"].get(key)
        result[key] = round(float(right) - float(left), 6) if isinstance(left, (int, float)) and isinstance(right, (int, float)) else None
    return result


def main() -> int:
    if len(FIRST_NOTE) not in range(30, 81) or len(SECOND_NOTE) not in range(30, 81):
        raise RuntimeError("interview_note_length_out_of_range")

    # 此数据库由运行命令明确指向 /tmp；先建表、再写入固定虚构种子。
    Base.metadata.drop_all(bind=engine)
    init_db()
    with SessionLocal() as db:
        seed_app_001(db)

    with TestClient(app) as client:
        # V1：作为 V2 的冻结基线。
        response = post(client, "/api/v1/applications/APP_001/workflows/scoring/run", "U_HR", "impact-v1")
        if response.status_code != 200:
            raise RuntimeError(f"v1_enqueue_failed:{response.status_code}:{response.text}")
        _assert_processed("v1")
        v1 = _snapshot("screening")

        # 题单：V2 只能使用确认后的题单绑定，不允许跨版本读取“最新题目”。
        for path, user, key in (
            ("/api/v1/applications/APP_001/actions/approve-first-interview", "U_DEPT_RECRUITER", "impact-approve-first"),
            ("/api/v1/applications/APP_001/workflows/first-interview-planning/run", "U_DEPT_RECRUITER", "impact-plan"),
        ):
            response = post(client, path, user, key)
            if response.status_code != 200:
                raise RuntimeError(f"first_interview_action_failed:{response.status_code}:{response.text}")
        _assert_processed("first_interview_plan")
        response = post(client, "/api/v1/applications/APP_001/workflows/first-interview-planning/confirm", "U_DEPT_RECRUITER", "impact-confirm-plan")
        if response.status_code != 200:
            raise RuntimeError(f"first_plan_confirm_failed:{response.status_code}:{response.text}")
        response = post(client, "/api/v1/applications/APP_001/actions/start-first-interview", "U_DEPT_RECRUITER", "impact-start-first")
        if response.status_code != 200:
            raise RuntimeError(f"first_interview_start_failed:{response.status_code}:{response.text}")
        response = post(client, "/api/v1/applications/APP_001/actions/finish-first-interview", "U_DEPT_RECRUITER", "impact-finish-first")
        if response.status_code != 200:
            raise RuntimeError(f"first_interview_finish_failed:{response.status_code}:{response.text}")

        # V2：提交常见正向一面评价，并发布新的 AAV/IPR。
        response = post(
            client,
            "/api/v1/applications/APP_001/actions/submit-first-feedback",
            "U_DEPT_RECRUITER",
            "impact-v2",
            {"rawNotes": FIRST_NOTE, "assessmentSummary": "验证了候选人的 Agent 工程能力。"},
        )
        if response.status_code != 200:
            raise RuntimeError(f"v2_enqueue_failed:{response.status_code}:{response.text}")
        _assert_processed("v2")
        v2 = _snapshot("after_first_interview")

        # V3：提交常见负向二面追问评价，验证后一版能基于 V2 再次更新。
        response = post(client, "/api/v1/applications/APP_001/actions/approve-second-interview", "U_HR", "impact-approve-second")
        if response.status_code != 200:
            raise RuntimeError(f"second_interview_approve_failed:{response.status_code}:{response.text}")
        response = post(
            client,
            "/api/v1/applications/APP_001/actions/finish-second-interview",
            "U_HR",
            "impact-finish-second",
            {"rawNotes": "完成二面，进入面评确认。"},
        )
        if response.status_code != 200:
            raise RuntimeError(f"second_interview_finish_failed:{response.status_code}:{response.text}")
        response = post(
            client,
            "/api/v1/applications/APP_001/actions/submit-second-feedback",
            "U_HR",
            "impact-v3",
            {"rawNotes": SECOND_NOTE, "assessmentSummary": "二面技术追问发现关键能力风险。"},
        )
        if response.status_code != 200:
            raise RuntimeError(f"v3_enqueue_failed:{response.status_code}:{response.text}")
        _assert_processed("v3")
        v3 = _snapshot("after_second_interview")

        # 只验证真实页面读取接口：初筛、二面工作台与最终审核，不保留孤立评估端点。
        workspace_statuses: dict[str, int] = {}
        for stage, path in {
            "screening": "/api/v1/applications/APP_001/views/screening-review",
            "after_first_interview": "/api/v1/applications/APP_001/views/hr-second-review",
            "after_second_interview": "/api/v1/applications/APP_001/views/final-review",
        }.items():
            view = client.get(path, headers=auth_headers(client, "U_HR"))
            workspace_statuses[stage] = view.status_code
            if view.status_code != 200:
                raise RuntimeError(f"formal_view_failed:{stage}:{view.status_code}:{view.text}")
        final_view = client.get(
            "/api/v1/applications/APP_001/views/final-review",
            headers=auth_headers(client, "U_HR"),
        )
        if final_view.status_code != 200:
            raise RuntimeError(f"final_review_failed:{final_view.status_code}:{final_view.text}")
        if final_view.json().get("viewSchemaVersion") != "final_review_v2":
            raise RuntimeError("final_review_schema_invalid")

    print(json.dumps({
        "test": "v2_v3_score_impact",
        "notes": {"v2Length": len(FIRST_NOTE), "v3Length": len(SECOND_NOTE)},
        "v1": v1,
        "v2": v2,
        "v3": v3,
        "v1ToV2Delta": _delta(v1, v2),
        "v2ToV3Delta": _delta(v2, v3),
        "assessmentWorkspaceStatus": workspace_statuses,
        "finalReviewSchemaVersion": final_view.json().get("viewSchemaVersion"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
