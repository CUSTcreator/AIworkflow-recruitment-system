"""数据库业务锁管理器。

所有需要同时修改共享业务对象的事务都应通过本模块取得 PostgreSQL 行锁。
锁管理器只负责按固定顺序执行 ``SELECT ... FOR UPDATE``，不负责业务状态
修改、外部调用或提交事务。固定顺序可以避免不同写路径互相反向持锁造成死锁。
"""
from __future__ import annotations

from time import perf_counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.infrastructure.observability.logging import get_logger, log_event
from backend.app.models.entities import (
    Application,
    Candidate,
    Interview,
    Job,
    WorkflowActivityCheckpoint,
    WorkflowArtifact,
    WorkflowRun,
    WorkflowStepCheckpoint,
)

logger = get_logger(__name__)


class BusinessLockError(RuntimeError):
    """请求的业务对象不存在，或无法取得一致性锁。"""


def _lock_one(db: Session, model: Any, key: Any, field: Any, label: str) -> Any:
    started = perf_counter()
    row = db.scalar(select(model).where(field == key).with_for_update())
    waited_ms = round((perf_counter() - started) * 1000, 2)
    if row is None:
        raise BusinessLockError(f"{label}_not_found:{key}")
    if waited_ms >= 100:
        log_event(logger, 30, "database_row_lock_wait", resource=label, key=str(key), wait_ms=waited_ms)
    return row


def lock_candidate(db: Session, candidate_id: str) -> Candidate:
    """锁定 Candidate 聚合根。"""
    return _lock_one(db, Candidate, candidate_id, Candidate.candidate_id, "candidate")


def lock_job(db: Session, job_id: str) -> Job:
    """锁定 Job 聚合根。"""
    return _lock_one(db, Job, job_id, Job.job_id, "job")


def lock_application(db: Session, application_id: str) -> Application:
    """锁定 Application 聚合根，版本分配和状态转换前必须调用。"""
    return _lock_one(db, Application, application_id, Application.application_id, "application")


def lock_interview(db: Session, interview_id: str) -> Interview:
    """锁定 Interview 聚合根。"""
    return _lock_one(db, Interview, interview_id, Interview.interview_id, "interview")


def lock_workflow_run(db: Session, workflow_run_id: str) -> WorkflowRun:
    """按统一顺序锁定 WorkflowRun。"""
    return _lock_one(db, WorkflowRun, workflow_run_id, WorkflowRun.workflow_run_id, "workflow_run")


def lock_step_checkpoint(db: Session, workflow_run_id: str, step_name: str) -> WorkflowStepCheckpoint:
    """锁定 WorkflowStepCheckpoint；调用方应先锁业务聚合根和 WorkflowRun。"""
    row = db.scalar(
        select(WorkflowStepCheckpoint)
        .where(
            WorkflowStepCheckpoint.workflow_run_id == workflow_run_id,
            WorkflowStepCheckpoint.step_name == step_name,
        )
        .with_for_update()
    )
    if row is None:
        raise BusinessLockError(f"step_checkpoint_not_found:{workflow_run_id}:{step_name}")
    return row


def lock_activity_checkpoint(db: Session, workflow_run_id: str, parent_step_name: str, activity_key: str) -> WorkflowActivityCheckpoint:
    """锁定 Activity checkpoint；调用方应先锁 Step checkpoint。"""
    row = db.scalar(
        select(WorkflowActivityCheckpoint)
        .where(
            WorkflowActivityCheckpoint.workflow_run_id == workflow_run_id,
            WorkflowActivityCheckpoint.parent_step_name == parent_step_name,
            WorkflowActivityCheckpoint.activity_key == activity_key,
        )
        .with_for_update()
    )
    if row is None:
        raise BusinessLockError(f"activity_checkpoint_not_found:{workflow_run_id}:{activity_key}")
    return row


def lock_business_resources(
    db: Session,
    *,
    candidate_id: str | None = None,
    job_id: str | None = None,
    application_id: str | None = None,
    interview_id: str | None = None,
    workflow_run_id: str | None = None,
) -> dict[str, Any]:
    """按 Candidate → Job → Application → Interview → WorkflowRun 顺序加锁。

    只锁定调用方实际传入的资源，不会锁整张表。返回已锁定对象，调用方可以
    直接复用，避免再次查询。Step/Activity checkpoint 由专用函数在 WorkflowRun
    之后锁定。
    """
    locked: dict[str, Any] = {}
    if candidate_id:
        locked["candidate"] = lock_candidate(db, candidate_id)
    if job_id:
        locked["job"] = lock_job(db, job_id)
    if application_id:
        locked["application"] = lock_application(db, application_id)
    if interview_id:
        locked["interview"] = lock_interview(db, interview_id)
    if workflow_run_id:
        locked["workflow_run"] = lock_workflow_run(db, workflow_run_id)
    return locked

