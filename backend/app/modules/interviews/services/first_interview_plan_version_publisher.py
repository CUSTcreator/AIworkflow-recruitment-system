"""一面题单版本发布器。"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.entities import (
    Application,
    ApplicationAssessmentVersion,
    FirstInterviewPlanVersion,
    StageHistory,
    User,
)
from backend.app.modules.applications.application_recovery import clear_application_recovery
from recruitment_ai_core.first_interview_planning import (
    FirstInterviewPlanPresentationResult,
    QuestionPlanningConstraintSet,
    QuestionProposalGenerationResult,
    QuestionRuleDerivationResult,
)
from recruitment_ai_core.first_interview_planning.contracts import FirstInterviewPlanningInput


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12].upper()}"


def _hash(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class FirstInterviewPlanVersionPublisher:
    """把一次题单规划的四层结果原子发布为唯一的 FirstInterviewPlanVersion。

    source_json 保存冻结来源；core_result_json 保存约束与 LLM 原始提案；rule_result_json 保存可执行
    业务事实；presentation_json 保存前端 DTO。四段同属一个版本，禁止拆成多个业务表或 WorkflowArtifact。
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    def publish(
        self,
        *,
        user: User,
        app: Application,
        source_assessment: ApplicationAssessmentVersion,
        template: dict[str, Any] | None,
        planning_input: FirstInterviewPlanningInput,
        constraints: QuestionPlanningConstraintSet,
        proposal_result: QuestionProposalGenerationResult,
        rule: QuestionRuleDerivationResult,
        presentation: FirstInterviewPlanPresentationResult,
    ) -> FirstInterviewPlanVersion:
        if app.status != "first_interview_planning":
            raise RuntimeError(f"first_interview_planning_status_changed:{app.status}")
        previous = self._latest(app.application_id)
        # 重跑永远新建版本；旧 draft 仅退出当前草稿位置，仍保留完整审计快照。
        if previous is not None and previous.status == "draft":
            previous.status = "superseded"
        now = datetime.now(UTC).replace(tzinfo=None)
        metadata = dict(planning_input.metadata or {})
        open_target_ids = [str(item.get("interview_target_id") or "") for item in constraints.target_snapshots]
        # 1. source_json 只记录冻结来源及其哈希，足以确认本版本基于哪一版 V1、模板与目标集合。
        source = {
            "schemaVersion": "first_interview_plan_source_v2",
            "sourceAssessmentVersionId": source_assessment.assessment_version_id,
            "sourceAssessmentVersion": source_assessment.version,
            "sourceAssessmentHash": (source_assessment.source_json or {}).get("sourceInputHash"),
            "openInterviewTargetIds": open_target_ids,
            "openInterviewTargetHash": _hash({"targetIds": open_target_ids, "targets": constraints.target_snapshots}),
            "templateId": (template or {}).get("templateId"),
            "templateVersionId": (template or {}).get("templateVersionId"),
            "templateVersion": (template or {}).get("version"),
            "planningPolicyVersion": metadata.get("planning_policy_version"),
            "promptVersion": "first_interview_planning_prompt_v2_0",
        }
        source["sourceInputHash"] = _hash(source)
        # 2. core_result_json 保存可重放的约束集与 LLM 原始提案；它们尚未成为可执行题单。
        core_result = {
            "schemaVersion": "first_interview_question_proposal_v2",
            "constraintSet": asdict(constraints),
            "questionDraftProposals": [asdict(item) for item in proposal_result.proposals],
            "generationMode": proposal_result.generation_mode,
            "generationWarnings": list(proposal_result.generation_warnings),
        }
        # 3. rule_result_json 才是可执行题单的业务事实：绑定、ID、Rubric 与覆盖检查都已确定。
        rule_result = {
            "schemaVersion": "first_interview_question_rule_v2",
            **asdict(rule),
        }
        # 4. presentation_json 是前端确认页 DTO；不能反向作为算法或规则输入。
        presentation_result = {
            "schemaVersion": "first_interview_plan_presentation_v2",
            **asdict(presentation),
        }
        row = FirstInterviewPlanVersion(
            plan_version_id=_id("FIP"),
            application_id=app.application_id,
            source_assessment_version_id=source_assessment.assessment_version_id,
            previous_plan_version_id=previous.plan_version_id if previous else None,
            template_version_id=(template or {}).get("templateVersionId"),
            version=(previous.version + 1) if previous else 1,
            status="draft",
            source_json=source,
            core_result_json=core_result,
            rule_result_json=rule_result,
            presentation_json=presentation_result,
            published_at=now,
        )
        self.db.add(row)
        self.db.add(StageHistory(
            stage_history_id=_id("SH"),
            application_id=app.application_id,
            actor_role=user.role,
            actor_name=user.display_name,
            action="publish_first_interview_plan",
            from_status=app.status,
            to_status=app.status,
            note=f"已发布一面题单规划 V{row.version}，等待面试官确认。",
            effective_at=now,
            business_timezone="Asia/Shanghai",
        ))
        clear_application_recovery(app)
        self.db.flush()
        return row

    def _latest(self, application_id: str) -> FirstInterviewPlanVersion | None:
        return self.db.scalars(
            select(FirstInterviewPlanVersion)
            .where(FirstInterviewPlanVersion.application_id == application_id)
            .order_by(FirstInterviewPlanVersion.version.desc(), FirstInterviewPlanVersion.created_at.desc())
        ).first()
