"""岗位主生命周期状态机：只管理 Job 的草稿、开放与关闭。"""
from __future__ import annotations

from enum import StrEnum

from backend.app.shared.errors import BusinessRuleError


class JobStatus(StrEnum):
    DRAFT = "draft"
    SETUP_PENDING = "setup_pending"
    OPEN = "open"
    CLOSED = "closed"


class JobAction(StrEnum):
    PUBLISH = "publish"
    CLOSE = "close"
    REOPEN = "reopen"
    REQUIRE_SETUP = "require_setup"


_TRANSITIONS: dict[tuple[str, str], str] = {
    (JobStatus.DRAFT.value, JobAction.PUBLISH.value): JobStatus.OPEN.value,
    (JobStatus.SETUP_PENDING.value, JobAction.PUBLISH.value): JobStatus.OPEN.value,
    # 撤销开放岗位的必要人员配置时，岗位回到已确认但待配置的状态。
    (JobStatus.OPEN.value, JobAction.REQUIRE_SETUP.value): JobStatus.SETUP_PENDING.value,
    (JobStatus.OPEN.value, JobAction.CLOSE.value): JobStatus.CLOSED.value,
    (JobStatus.CLOSED.value, JobAction.REOPEN.value): JobStatus.OPEN.value,
}


def transition_job(job, action: JobAction | str) -> str:
    """校验并写入岗位生命周期状态。"""
    current = str(job.status or JobStatus.DRAFT.value)
    action_value = str(action)
    target = _TRANSITIONS.get((current, action_value))
    if target is None:
        raise BusinessRuleError(
            status_code=409,
            code="invalid_job_transition",
            detail=f"岗位不允许从 {current} 执行动作 {action_value}",
        )
    job.status = target
    return target