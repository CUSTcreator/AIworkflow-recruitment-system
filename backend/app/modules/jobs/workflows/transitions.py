"""岗位画像 workflow 的失败与重试状态收尾。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from backend.app.models.entities import JobVersionRecord
from backend.app.modules.jobs.job_process_service import JobProcessService, JobProfileStatus
from backend.app.shared.workflows import WorkflowTransitionContext
from backend.app.modules.jobs.job_recovery import JobRecoveryCode


def handle_job_profile_blocked(context: WorkflowTransitionContext) -> None:
    """将岗位画像输入不足投影为待确认，而不是技术失败。"""
    db: Any = context.metadata["db"]
    run: Any = context.metadata["run"]
    if run.subject_type != "job_version" or not run.subject_id:
        return
    version = db.get(JobVersionRecord, run.subject_id)
    if version is None:
        return
    JobProcessService.mark_profile(
        version,
        status=JobProfileStatus.REVIEW_REQUIRED,
        now=context.metadata["now"],
        reason=str(context.error)[:2000],
    )
    version.recovery_code = JobRecoveryCode.PROFILE_REVIEW_REQUIRED.value
    version.recovery_context_json = {
        "blockedStep": context.metadata.get("step_name"),
        "errorCode": context.metadata.get("error_code"),
    }


def handle_job_profile_transition(context: WorkflowTransitionContext) -> None:
    """把终态运行失败投影到受影响 JD 版本，而不改变 Job 的开放/关闭状态。

    Step 的临时退避不是一次新的业务排队：此前 ``freeze_job_version`` 已把该版本
    标记为 ``processing``。重试时若再回写 ``queued``，页面会错误显示“尚未开始”，
    并丢失“正在生成、等待重试”的语义。等待信息由 WorkflowRun/Checkpoint 表达，
    这里只在终态失败时写入 ``failed``。
    """
    db: Any = context.metadata["db"]
    run: Any = context.metadata["run"]
    now: datetime = context.metadata["now"]
    if run.subject_type != "job_version" or not run.subject_id:
        return
    version = db.get(JobVersionRecord, run.subject_id)
    if version is None:
        return
    if context.retrying:
        return
    step_name = str(context.metadata.get("step_name") or "")
    code = str(context.metadata.get("error_code") or "")
    version.recovery_code = (
        JobRecoveryCode.PROFILE_READY_RETRYABLE.value
        if step_name == "mark_job_version_ready"
        else JobRecoveryCode.PROFILE_RETRYABLE.value
    )
    version.recovery_context_json = {"failedStep": step_name or None, "errorCode": code or None}
    JobProcessService.mark_profile(
        version,
        status=JobProfileStatus.FAILED,
        now=now,
        reason=str(context.error)[:2000],
    )
