from __future__ import annotations

from datetime import datetime
from typing import Any

from backend.app.models.entities import Application, User
from backend.app.shared.workflows import WorkflowTransitionContext
from backend.app.modules.assessment.hard_screening.hard_screening_service import HardScreeningService
from backend.app.modules.assessment.services.assessment_version_publisher import AssessmentVersionPublisher


def transition_hard_screening(
    db: Any, run: Any, _now: datetime, error: Exception, retrying: bool,
    *,
    error_code: str = "",
    error_category: str = "",
    step_name: str = "",
    recovery_action: str = "",
    blocked: bool = False,
) -> None:
    if retrying:
        return
    HardScreeningService(db).record_processing_failure(
        run,
        error,
        error_code=error_code,
        error_category=error_category,
        step_name=step_name,
        recovery_action=recovery_action,
        blocked=blocked,
    )


def transition_hard_screening_blocked(
    db: Any, run: Any, _now: datetime, error: Exception, retrying: bool,
    **details: Any,
) -> None:
    """硬筛业务阻塞也要落到人工复核状态，避免任务停在 processing。"""
    transition_hard_screening(
        db, run, _now, error, retrying, blocked=True, **details,
    )


def transition_screening(
    db: Any, run: Any, _now: datetime, error: Exception, retrying: bool,
    *,
    error_code: str = "",
    error_category: str = "",
    step_name: str = "",
    recovery_action: str = "",
    blocked: bool = False,
) -> None:
    if retrying or not run.application_id:
        return
    app = db.get(Application, run.application_id)
    if app is None or app.status != "screening_running":
        return
    user = db.get(User, run.triggered_by)
    if user is None:
        raise RuntimeError("workflow_user_not_found")
    AssessmentVersionPublisher(db).record_screening_failure(
        user,
        app,
        error,
        error_code=error_code,
        error_category=error_category,
        step_name=step_name,
        recovery_action=recovery_action,
        blocked=blocked,
        workflow_run_id=str(run.workflow_run_id or ""),
    )


def transition_screening_blocked(
    db: Any, run: Any, _now: datetime, error: Exception, retrying: bool,
    **details: Any,
) -> None:
    """把冻结来源的业务阻塞落为可重试的申请状态。"""
    transition_screening(
        db, run, _now, error, retrying, blocked=True, **details,
    )


def _transition_args(context: WorkflowTransitionContext):
    return (
        context.metadata["db"],
        context.metadata["run"],
        context.metadata["now"],
        context.error,
        context.retrying,
    )


def _transition_details(context: WorkflowTransitionContext) -> dict[str, Any]:
    """StepRunner 已统一分类；领域 Transition 只消费，不再解析异常字符串。"""

    return {
        "error_code": str(context.metadata.get("error_code") or ""),
        "error_category": str(context.metadata.get("error_category") or ""),
        "step_name": str(context.metadata.get("step_name") or ""),
        "recovery_action": str(context.recovery_action or ""),
    }


def handle_hard_screening_transition(context: WorkflowTransitionContext) -> None:
    transition_hard_screening(*_transition_args(context), **_transition_details(context))


def handle_hard_screening_blocked_transition(context: WorkflowTransitionContext) -> None:
    transition_hard_screening_blocked(*_transition_args(context), **_transition_details(context))


def handle_screening_transition(context: WorkflowTransitionContext) -> None:
    transition_screening(*_transition_args(context), **_transition_details(context))


def handle_screening_blocked_transition(context: WorkflowTransitionContext) -> None:
    transition_screening_blocked(*_transition_args(context), **_transition_details(context))

