from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select

from backend.app.models.entities import (
    Application,
    ApplicationAssessmentVersion,
    Interview,
)
from backend.app.modules.applications.domain.application_lifecycle_policy import is_application_closed
from backend.app.modules.applications.application_recovery import (
    ApplicationRecoveryCode,
    set_application_recovery,
)
from backend.app.shared.workflows import WorkflowTransitionContext
from backend.app.modules.interviews.commands.interview_record_commands import InterviewRecordCommands


def transition_first_planning(
    db: Any, run: Any, _now: datetime, _error: Exception, retrying: bool,
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
    if app is None or app.status != "first_interview_planning":
        return
    set_application_recovery(
        app,
        _first_planning_recovery_code(error_code=error_code, step_name=step_name),
        context={
            "workflowRunId": str(run.workflow_run_id or "") or None,
            "failedStep": step_name or None,
            "errorCode": error_code or None,
            "errorCategory": error_category or None,
            "runtimeRecoveryAction": recovery_action or None,
            "blocked": blocked,
        },
    )


def _first_planning_recovery_code(
    *, error_code: str, step_name: str,
) -> ApplicationRecoveryCode:
    """把运行时错误分类为稳定业务恢复码，页面无需解析异常正文。"""

    code = str(error_code or "").casefold()
    if code == "first_interview_source_assessment_missing":
        return ApplicationRecoveryCode.FIRST_INTERVIEW_SOURCE_ASSESSMENT_REQUIRED
    if code in {"first_interview_source_profile_missing", "application_job_missing"}:
        return ApplicationRecoveryCode.FIRST_INTERVIEW_SOURCE_PROFILE_REQUIRED
    if code == "first_interview_open_target_limit_exceeded":
        return ApplicationRecoveryCode.FIRST_INTERVIEW_TARGET_REVIEW_REQUIRED
    if code == "first_interview_generation_config_invalid":
        return ApplicationRecoveryCode.FIRST_INTERVIEW_CONFIGURATION_REQUIRED
    if step_name == "publish_first_interview_plan" or code.startswith("publish:"):
        return ApplicationRecoveryCode.FIRST_INTERVIEW_PUBLISH_RETRYABLE
    return ApplicationRecoveryCode.FIRST_INTERVIEW_PLANNING_RETRYABLE


def transition_first_planning_blocked(
    db: Any, run: Any, now: datetime, error: Exception, retrying: bool, **details: Any,
) -> None:
    transition_first_planning(
        db, run, now, error, retrying, blocked=True, **details,
    )


def transition_first_scoring(
    db: Any, run: Any, _now: datetime, _error: Exception, retrying: bool,
    **details: Any,
) -> None:
    _transition_scoring(db, run, "FIRST", "first", retrying, _error, **details)


def transition_second_scoring(
    db: Any, run: Any, _now: datetime, _error: Exception, retrying: bool,
    **details: Any,
) -> None:
    _transition_scoring(db, run, "SECOND", "second", retrying, _error, **details)


def _transition_scoring(
    db: Any, run: Any, suffix: str, stage: str, retrying: bool,
    error: Exception,
    *, error_code: str = "", error_category: str = "", step_name: str = "",
    recovery_action: str = "", blocked: bool = False,
) -> None:
    app = db.get(Application, run.application_id) if run.application_id else None
    if app is None:
        return
    # 申请一旦已经被业务决策终止，后续旧 Workflow 的失败不能重新打开
    # “待确认”入口，也不能覆盖已经保存的不通过决定。
    if is_application_closed(app.status):
        return
    interview = db.get(Interview, f"INT_{app.application_id}_{suffix}")
    if interview is not None:
        # ``blocked`` 表示评分输入等待用户确认，原始面评仍然有效；不能把它
        # 投影成面试提交失败，否则任务中心会给出与恢复动作相反的状态。
        interview.status = "submitted" if retrying or blocked else "failed"
    if not retrying:
        previous_stage = "screening" if stage == "first" else "after_first_interview"
        source_assessment = db.scalars(
            select(ApplicationAssessmentVersion)
            .where(
                ApplicationAssessmentVersion.application_id == app.application_id,
                ApplicationAssessmentVersion.stage == previous_stage,
            )
            .order_by(
                ApplicationAssessmentVersion.version.desc(),
                ApplicationAssessmentVersion.created_at.desc(),
            )
        ).first()
        set_application_recovery(
            app,
            _post_interview_recovery_code(
                stage=stage,
                error_code=error_code,
                step_name=step_name,
                error_message=str(error or ""),
            ),
            context={
                "workflowRunId": str(run.workflow_run_id or "") or None,
                "failedStep": step_name or None,
                "errorCode": error_code or None,
                "errorCategory": error_category or None,
                "runtimeRecoveryAction": recovery_action or None,
                "assessmentStage": "after_first_interview" if stage == "first" else "after_second_interview",
                # 来源修复必须能证明上游正式版本已经变化；否则“重试”只会
                # 再次冻结同一份坏 V1/V2，并把申请送回原异常。
                "sourceAssessmentVersionId": (
                    source_assessment.assessment_version_id
                    if source_assessment is not None else None
                ),
                "blocked": blocked,
            },
        )
        if not blocked:
            InterviewRecordCommands(db).mark_task_failed(app.application_id, stage)


def _post_interview_recovery_code(
    *, stage: str, error_code: str, step_name: str, error_message: str = "",
) -> ApplicationRecoveryCode:
    """把七步运行错误映射为用户可执行的稳定恢复码。"""
    first = stage == "first"
    code = str(error_code or "").casefold()
    step = str(step_name or "").casefold()
    message = str(error_message or "").casefold()
    detail = f"{code} {message}"
    if any(marker in detail for marker in (
        "record_set_changed",
        "parse_review_required",
        "anchor_input_too_large",
    )):
        return (
            ApplicationRecoveryCode.POST_FIRST_FEEDBACK_REVIEW_REQUIRED
            if first else ApplicationRecoveryCode.POST_SECOND_FEEDBACK_REVIEW_REQUIRED
        )
    source_code = any(
        marker in code for marker in (
            "source", "previous_version", "frozen_profile", "topology",
            "first_interview_plan", "confirmed_first_interview",
        )
    )
    source_message = message.startswith((
        "assessment_previous_",
        "assessment_frozen_",
        "post_interview_frozen_",
        "post_interview_source_",
        "topology_cycle_",
        "topology_definition_",
        "topology_snapshot_",
        "topology_state_",
    ))
    if step == "freeze_post_interview_sources" or source_code or source_message:
        return (
            ApplicationRecoveryCode.POST_FIRST_SOURCE_REVIEW_REQUIRED
            if first else ApplicationRecoveryCode.POST_SECOND_SOURCE_REVIEW_REQUIRED
        )
    # 解析步骤的模型/运行时瞬时失败可以直接重试；只有上面的确定性输入错误
    # 才要求用户改面评，避免把“修改后重算”当成所有解析异常的唯一出口。
    if step == "publish_post_interview_assessment" or code.startswith("publish:"):
        return (
            ApplicationRecoveryCode.POST_FIRST_PUBLISH_RETRYABLE
            if first else ApplicationRecoveryCode.POST_SECOND_PUBLISH_RETRYABLE
        )
    return (
        ApplicationRecoveryCode.POST_FIRST_SCORING_RETRYABLE
        if first else ApplicationRecoveryCode.POST_SECOND_SCORING_RETRYABLE
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
    return {
        "error_code": str(context.metadata.get("error_code") or ""),
        "error_category": str(context.metadata.get("error_category") or ""),
        "step_name": str(context.metadata.get("step_name") or ""),
        "recovery_action": str(context.recovery_action or ""),
    }


def handle_first_planning_transition(context: WorkflowTransitionContext) -> None:
    transition_first_planning(*_transition_args(context), **_transition_details(context))


def handle_first_planning_blocked_transition(context: WorkflowTransitionContext) -> None:
    transition_first_planning_blocked(
        *_transition_args(context), **_transition_details(context),
    )


def handle_first_scoring_transition(context: WorkflowTransitionContext) -> None:
    transition_first_scoring(*_transition_args(context), **_transition_details(context))


def handle_second_scoring_transition(context: WorkflowTransitionContext) -> None:
    transition_second_scoring(*_transition_args(context), **_transition_details(context))


def handle_first_scoring_blocked_transition(context: WorkflowTransitionContext) -> None:
    transition_first_scoring(
        *_transition_args(context), **_transition_details(context), blocked=True,
    )


def handle_second_scoring_blocked_transition(context: WorkflowTransitionContext) -> None:
    transition_second_scoring(
        *_transition_args(context), **_transition_details(context), blocked=True,
    )
