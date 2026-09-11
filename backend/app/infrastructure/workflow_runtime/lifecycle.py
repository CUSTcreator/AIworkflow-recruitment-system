"""
WorkflowRun 的通用状态收尾函数。所有函数只更新任务运行记录，不直接修改业务实体。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from backend.app.shared.workflows.status_contracts import (
    WorkflowRunStatus,
    ensure_workflow_transition,
)


def mark_completed(
    run: Any,
    now: datetime,
    *,
    message: str | None = None,
) -> None:
    """将任务标记为完成并释放租约；message 可记录非错误的完成说明。"""
    ensure_workflow_transition(run.status, WorkflowRunStatus.COMPLETED)
    run.status = WorkflowRunStatus.COMPLETED.value
    run.completed_at = now
    run.error_message = message
    _release_lease(run, now)


def mark_blocked(
    run: Any,
    now: datetime,
    *,
    message: str,
) -> None:
    """将任务标记为待用户确认并释放租约，避免留下不可领取的陈旧执行权。"""
    ensure_workflow_transition(run.status, WorkflowRunStatus.BLOCKED)
    run.status = WorkflowRunStatus.BLOCKED.value
    run.completed_at = now
    run.available_at = None
    run.error_message = message
    _release_lease(run, now)

def schedule_retry(
    run: Any,
    now: datetime,
    error: Exception,
    *,
    available_at: datetime,
) -> None:
    """将可重试失败重新排回 pending，并设置下一次允许领取的时间。"""
    ensure_workflow_transition(run.status, WorkflowRunStatus.PENDING)
    run.status = WorkflowRunStatus.PENDING.value
    run.completed_at = None
    run.available_at = available_at
    run.error_message = str(error)[:2000]
    _release_lease(run, now)


def mark_failed(run: Any, now: datetime, error: Exception) -> None:
    """将任务标记为最终失败并释放租约；业务状态回退由 transition_handler 完成。"""
    ensure_workflow_transition(run.status, WorkflowRunStatus.FAILED)
    run.status = WorkflowRunStatus.FAILED.value
    run.completed_at = now
    run.error_message = str(error)[:2000]
    _release_lease(run, now)


def _release_lease(run: Any, now: datetime) -> None:
    """统一清除租约；无论完成、重试还是失败，任务都不能长期被错误占用。"""
    run.lease_owner = None
    run.lease_expires_at = None
    run.heartbeat_at = now
    run.updated_at = now
