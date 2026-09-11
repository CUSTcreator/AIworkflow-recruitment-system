"""工作流执行时间线的业务安全读取。

本模块只投影可给 HR 看到的步骤状态、稳定文案和错误码。原始异常、外部回包及
``diagnostic_fields`` 仍只能由管理员在技术日志中查看，不能通过业务详情接口返回。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.entities import WorkflowExecutionEvent
from backend.app.infrastructure.observability.event_contracts import recovery_action_label, error_code_label
from backend.app.shared.time_serialization import normalize_utc_iso, utc_iso
from backend.app.shared.workflows.process_view import workflow_step_label
from backend.app.shared.workflows.error_recovery_policy import resolve_recovery
from backend.app.shared.workflows.step_contracts import StepOutcomeKind


class WorkflowExecutionTimelineQuery:
    """按已完成的权限校验范围读取简版运行轨迹。"""

    def __init__(self, db: Session) -> None:
        self.db = db

    @staticmethod
    def _view(event: WorkflowExecutionEvent) -> dict[str, Any]:
        """构造业务页面 DTO；禁止把诊断 JSON 原样透出。"""
        diagnostics = event.diagnostic_fields or {}
        outcome_kind = {
            "step_deferred": StepOutcomeKind.RETRY_WAIT,
            "step_blocked": StepOutcomeKind.BLOCKED,
            "step_failed": StepOutcomeKind.FAILED,
            "workflow_failed": StepOutcomeKind.FAILED,
        }.get(event.event_type)
        decision = resolve_recovery(
            error_code=event.error_code,
            error_category=event.error_category,
            outcome_kind=outcome_kind,
        )
        return {
            "executionEventId": event.execution_event_id,
            "workflowRunId": event.workflow_run_id,
            "occurredAt": utc_iso(event.occurred_at),
            "stepName": event.step_name or "",
            "stepLabel": workflow_step_label(event.step_name),
            "eventType": event.event_type,
            "severity": event.severity,
            "message": event.public_message,
            "attemptCount": event.attempt_count,
            "pollCount": event.poll_count,
            # safe_diagnostics 只允许该调度时间进入时间线，不能返回其余诊断字段。
            "nextAttemptAt": normalize_utc_iso(diagnostics.get("next_attempt_at")),
            "errorCategory": event.error_category or "",
            "errorCode": event.error_code or "",
            "recoveryAction": str(diagnostics.get("recovery_action") or decision.action.value),
            "recoveryActionLabel": recovery_action_label(str(diagnostics.get("recovery_action") or decision.action.value)),
            "errorCodeLabel": error_code_label(event.error_code),
        }

    def for_resume_submission(self, *, submission_id: str, limit: int = 20) -> list[dict[str, Any]]:
        events = list(self.db.scalars(
            select(WorkflowExecutionEvent)
            .where(WorkflowExecutionEvent.resume_submission_id == submission_id)
            .order_by(WorkflowExecutionEvent.occurred_at.desc(), WorkflowExecutionEvent.execution_event_id.desc())
            .limit(limit)
        ))
        return [self._view(event) for event in reversed(events)]

    def for_workflow_run(self, *, workflow_run_id: str, limit: int = 20) -> list[dict[str, Any]]:
        events = list(self.db.scalars(
            select(WorkflowExecutionEvent)
            .where(WorkflowExecutionEvent.workflow_run_id == workflow_run_id)
            .order_by(WorkflowExecutionEvent.occurred_at.desc(), WorkflowExecutionEvent.execution_event_id.desc())
            .limit(limit)
        ))
        return [self._view(event) for event in reversed(events)]

