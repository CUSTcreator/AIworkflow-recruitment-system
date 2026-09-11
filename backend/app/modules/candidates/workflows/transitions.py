"""候选人岗位分发 Workflow 的失败、重试结果投影。

本文件只把 Worker 终态交给 CandidateIntakeProcessService 解释；不得再直接修改
Candidate.status，因为岗位分发不是候选人档案生命周期。
"""
from __future__ import annotations

from typing import Any

from backend.app.models.entities import ResumeSubmission
from backend.app.modules.candidates.intake_process_service import (
    CandidateIntakeProcessService,
    RoutingOutcomeStatus,
)
from backend.app.modules.candidates.domain.resume_submission_state_machine import (
    ResumeRecoveryCode,
)
from backend.app.shared.workflows import WorkflowTransitionContext


def handle_candidate_routing_transition(context: WorkflowTransitionContext) -> None:
    """将分发重试/失败写为独立结果，保留已完成的简历结构化事实。"""
    db: Any = context.metadata["db"]
    run: Any = context.metadata["run"]
    now = context.metadata["now"]
    if run.subject_type != "resume_submission" or not run.subject_id:
        return
    submission = db.get(ResumeSubmission, run.subject_id)
    if submission is None:
        return
    if context.retrying:
        CandidateIntakeProcessService.mark_routing(
            submission,
            status=RoutingOutcomeStatus.PROCESSING,
            reason="候选人岗位分发正在重试",
            now=now,
        )
        return
    CandidateIntakeProcessService.mark_routing(
        submission,
        status=RoutingOutcomeStatus.FAILED,
        reason="岗位分发失败，可重试或人工选择岗位",
        now=now,
        recovery_code=ResumeRecoveryCode.ROUTING_RETRYABLE.value,
        recovery_context={
            "failed_step": str(context.metadata.get("step_name") or ""),
            "error_code": str(context.metadata.get("error_code") or ""),
        },
    )
    submission.error_message = str(context.error)[:2000]


def handle_candidate_routing_blocked(context: WorkflowTransitionContext) -> None:
    """自动岗位匹配无法可靠完成时，转为用户可执行的人工选岗。"""
    db: Any = context.metadata["db"]
    run: Any = context.metadata["run"]
    now = context.metadata["now"]
    if run.subject_type != "resume_submission" or not run.subject_id:
        return
    submission = db.get(ResumeSubmission, run.subject_id)
    if submission is None:
        return
    CandidateIntakeProcessService.mark_routing(
        submission,
        status=RoutingOutcomeStatus.MANUAL_SELECTION_AVAILABLE,
        reason="自动岗位匹配需要人工选择岗位",
        now=now,
        recovery_code=ResumeRecoveryCode.ROUTING_MANUAL_SELECTION.value,
        recovery_context={
            "blocked_step": str(context.metadata.get("step_name") or "match_candidate_jobs"),
            "error_code": str(context.metadata.get("error_code") or ""),
        },
    )
