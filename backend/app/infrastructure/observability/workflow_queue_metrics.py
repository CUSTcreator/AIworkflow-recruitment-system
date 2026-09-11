"""Workflow 队列健康度采样。

指标在 /metrics 被抓取时即时从数据库读取，因此即使 Worker 停止，也能观察到
待处理任务、失败任务和过期租约的积压。采样只读数据库，不改变 Workflow 状态。
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.infrastructure.observability.metrics import metrics
from backend.app.models.entities import WorkflowRun


def observe_workflow_queue(db: Session) -> None:
    """刷新 Workflow 队列、运行中、失败和过期租约四个瞬时指标。"""
    now = datetime.now(UTC).replace(tzinfo=None)
    for status in ("pending", "running", "failed"):
        total = int(
            db.scalar(
                select(func.count()).select_from(WorkflowRun).where(
                    WorkflowRun.status == status
                )
            )
            or 0
        )
        metrics.set_gauge("recruit_workflow_runs", total, status=status)
    expired = int(
        db.scalar(
            select(func.count()).select_from(WorkflowRun).where(
                WorkflowRun.status == "running",
                WorkflowRun.lease_expires_at.is_not(None),
                WorkflowRun.lease_expires_at <= now,
            )
        )
        or 0
    )
    metrics.set_gauge("recruit_workflow_expired_leases", expired)