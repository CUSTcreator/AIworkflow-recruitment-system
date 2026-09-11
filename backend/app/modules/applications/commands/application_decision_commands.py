"""Application 人工决策命令：处理部门、HR 与最终招聘决定。"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.entities import (
    Application,
    ApplicationAssessmentVersion,
    HumanDecision,
    StageHistory,
    Task,
    User,
)
from backend.app.shared.audit import record_audit_event
from backend.app.modules.auth.public import assert_permission
from backend.app.modules.candidates.public import CandidateIntakeService
from backend.app.shared.errors import BusinessError
from backend.app.modules.applications.application_process_service import ApplicationProcessService
from backend.app.modules.applications.application_recovery import clear_application_recovery


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12].upper()}"


class ApplicationDecisionCommands:
    """Human decisions that move an application between recruitment stages."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def final_decision(
        self,
        user: User,
        app: Application,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        decision = body.get("decision", "offer_process")
        action = {
            "offer_process": "offer",
            "closed_rejected": "reject",
            "manual_review": "manual_review",
        }.get(decision)
        if action is None:
            raise BusinessError("final_decision_invalid", "无效的最终决策")
        # 终审页面可在二面提交后先行展示 V2，但最终决定必须有已发布的 V3 作为
        # 正式依据。DTO 隐藏按钮只是体验层；这里是不能绕过的命令端不变量。
        has_published_v3 = self.db.scalar(
            select(ApplicationAssessmentVersion.assessment_version_id).where(
                ApplicationAssessmentVersion.application_id == app.application_id,
                ApplicationAssessmentVersion.stage == "after_second_interview",
                ApplicationAssessmentVersion.published_at.is_not(None),
            ).limit(1)
        )
        if has_published_v3 is None:
            raise BusinessError(
                "final_decision_assessment_pending",
                "二面后评估尚未正式发布，暂不能作出最终决定。",
                status_code=409,
            )
        return self._decide(
            user,
            app,
            action,
            f"最终决策：{decision}",
            authorization_action="final_decision",
            after=lambda: self._complete_task(app.application_id, "make_final_decision"),
        )

    def department_decision(
        self,
        user: User,
        app: Application,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        decision = body.get("decision")
        action = {
            "暂缓": "hold",
            "不推进": "reject",
            "人工复核": "manual_review",
        }.get(decision)
        if action is None:
            raise BusinessError("department_decision_invalid", "无效的部门审核决策")
        return self._decide(
            user,
            app,
            action,
            f"部门审核决策：{decision}",
            authorization_action="department_decision",
        )

    def hr_decision(
        self,
        user: User,
        app: Application,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        decision = body.get("decision")
        action = {
            "暂缓": "hold",
            "不推进": "reject",
            "补充验证": "manual_review",
        }.get(decision)
        if action is None:
            raise BusinessError("hr_decision_invalid", "无效的 HR 审核决策")
        return self._decide(
            user,
            app,
            action,
            f"HR 审核决策：{decision}",
            authorization_action="hr_decision",
        )

    def _decide(
        self,
        user: User,
        app: Application,
        action: str,
        reason: str,
        *,
        authorization_action: str,
        after: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        # CommandRunner 是正常入口；服务层仍用同一个阶段授权动作防御直接调用。
        # state action 只用于状态机，不能再次拿 reject 等通用名称推断权限。
        assert_permission(self.db, user, app, authorization_action)
        now = datetime.now(UTC).replace(tzinfo=None)
        from_status = app.status
        transition = ApplicationProcessService.plan_transition(app, action=action)
        to_status = transition.to_status
        self.db.add(
            HumanDecision(
                decision_id=_id("HD"),
                application_id=app.application_id,
                actor_role=user.role,
                actor_name=user.display_name,
                decision=action,
                from_status=from_status,
                to_status=to_status,
                reason=reason,
                effective_at=now,
                business_timezone="Asia/Shanghai",
            )
        )
        self.db.add(
            StageHistory(
                stage_history_id=_id("SH"),
                application_id=app.application_id,
                actor_role=user.role,
                actor_name=user.display_name,
                action=action,
                from_status=from_status,
                to_status=to_status,
                note=reason,
                effective_at=now,
                business_timezone="Asia/Shanghai",
            )
        )
        if after is not None:
            after()
        if to_status in {"offer_process", "closed_rejected"}:
            self._complete_all_tasks(app.application_id)

        ApplicationProcessService.apply_transition(
            app, transition, now=now, owner=user.display_name
        )
        if to_status in {"offer_process", "closed_rejected"}:
            # 业务终态优先于任何旧 Workflow 异常；清除过期恢复事实，
            # 避免招聘流程页在“不通过”后继续显示旧任务的“待确认”。
            clear_application_recovery(app)
            CandidateIntakeService(self.db).refresh_lifecycle(app.candidate_id)
        record_audit_event(
            self.db,
            actor=user,
            action="application.decision",
            target_type="application",
            target_id=app.application_id,
            summary=f"人工决策：{action}",
            details={
                "fromStatus": from_status,
                "toStatus": to_status,
                "reason": reason,
            },
        )
        self.db.flush()
        return {
            "application_id": app.application_id,
            "status": app.status,
            "message": "人工决策已保存",
        }

    def _complete_task(self, application_id: str, task_type: str) -> None:
        tasks = self.db.scalars(
            select(Task).where(
                Task.application_id == application_id,
                Task.task_type == task_type,
                Task.status != "done",
            )
        ).all()
        self._complete(tasks)

    def _complete_all_tasks(self, application_id: str) -> None:
        tasks = self.db.scalars(
            select(Task).where(
                Task.application_id == application_id,
                Task.status.in_(("pending", "in_progress")),
            )
        ).all()
        self._complete(tasks)

    @staticmethod
    def _complete(tasks: list[Task]) -> None:
        now = datetime.now(UTC).replace(tzinfo=None)
        for task in tasks:
            task.status = "done"
            task.completed_at = now
            task.updated_at = now



