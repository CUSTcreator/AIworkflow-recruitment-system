"""持久化工作流 Worker 的执行入口。负责从数据库领取任务、维持租约、调用业务处理器，并统一处理重试和最终失败。"""

from __future__ import annotations

import argparse
import logging
import os
import socket
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_, select, update

from backend.app.core.config import settings
from backend.app.bootstrap.model_gateway import configure_model_infrastructure
from backend.app.bootstrap.workflows import get_workflow_runtime
from backend.app.infrastructure.workflow_runtime.lifecycle import mark_failed, schedule_retry
from backend.app.infrastructure.workflow_runtime.step_runner import StepTerminalFailure
from backend.app.db.init_db import init_db
from backend.app.db.session import SessionLocal, engine
from backend.app.models.entities import WorkflowRun, WorkflowStepCheckpoint
from backend.app.shared.errors import retryable_decision
from backend.app.shared.workflows import StepErrorCategory, StepStatus, WorkflowRunStatus
from backend.app.shared.workflows.status_contracts import (
    ensure_step_transition,
    ensure_workflow_transition,
)
from backend.app.infrastructure.observability import configure_logging
from backend.app.infrastructure.observability.context import bind_context
from backend.app.infrastructure.observability.logging import log_event
from backend.app.infrastructure.observability.metrics import metrics


configure_model_infrastructure()


logger = logging.getLogger(__name__)
_RUNTIME = get_workflow_runtime()
_RETIRED_DEFINITION_WORKER = "workflow-definition-retirement"
_RETIRED_DEFINITION_ERROR_CODE = "workflow_definition_retired"

def _supported_workflow_types() -> frozenset[str]:
    """返回当前部署版本明确支持的任务类型，避免 Worker 领取未知的历史任务。"""
    return _RUNTIME.supported_types()


def _supports_workflow_run(run: WorkflowRun) -> bool:
    """Check the exact definition frozen on a persisted run, not only its type."""
    return _RUNTIME.supports(run.workflow_type, run.definition_version)


def _uses_step_recovery(
    workflow_type: str | None,
    definition_version: int | None = None,
) -> bool:
    """步骤式 Workflow 的重试上限由 checkpoint 管理，不使用旧的整任务次数上限。"""
    spec = _RUNTIME.registry.get(workflow_type, definition_version)
    return bool(spec and spec.steps)


def _legacy_workflow_types() -> frozenset[str]:
    """仍使用旧 handler 的任务类型，保留既有 WorkflowRun 最大重试次数语义。"""
    return frozenset(item for item in _supported_workflow_types() if not _uses_step_recovery(item))

def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def _eligible(now: datetime):
    """筛选可领取任务：尚未到期的待执行任务，或租约已经失效的中断任务。"""
    return or_(
        and_(
            WorkflowRun.status == WorkflowRunStatus.PENDING.value,
            or_(WorkflowRun.available_at.is_(None), WorkflowRun.available_at <= now),
        ),
        and_(
            WorkflowRun.status == WorkflowRunStatus.RUNNING.value,
            WorkflowRun.lease_expires_at.is_not(None),
            WorkflowRun.lease_expires_at <= now,
        ),
    )


def _workflow_subject(run: WorkflowRun) -> tuple[str, str] | None:
    if run.subject_type and run.subject_id:
        return run.subject_type, run.subject_id
    if run.application_id:
        return "application", run.application_id
    return None


def _lock_workflow_subject(db, run: WorkflowRun) -> None:
    subject = _workflow_subject(run)
    if subject is None:
        raise RuntimeError("workflow_subject_missing")
    subject_type, subject_id = subject
    _RUNTIME.lock_subject(db, subject_type, subject_id)


def _active_same_subject_query(run: WorkflowRun, now: datetime):
    subject = _workflow_subject(run)
    if subject is None:
        raise RuntimeError("workflow_subject_missing")
    subject_type, subject_id = subject
    if run.subject_type and run.subject_id:
        subject_filter = and_(
            WorkflowRun.subject_type == subject_type,
            WorkflowRun.subject_id == subject_id,
        )
    else:
        subject_filter = WorkflowRun.application_id == subject_id
    return select(WorkflowRun.workflow_run_id).where(
        subject_filter,
        WorkflowRun.workflow_run_id != run.workflow_run_id,
        WorkflowRun.status == WorkflowRunStatus.RUNNING.value,
        WorkflowRun.lease_expires_at.is_not(None),
        WorkflowRun.lease_expires_at > now,
    )


def _finalize_one_exhausted_lease(now: datetime) -> bool:
    with SessionLocal() as db:
        query = (
            select(WorkflowRun)
            .where(
                WorkflowRun.workflow_type.in_(_legacy_workflow_types()),
                WorkflowRun.status == WorkflowRunStatus.RUNNING.value,
                WorkflowRun.attempt_count >= WorkflowRun.max_attempts,
                WorkflowRun.lease_expires_at.is_not(None),
                WorkflowRun.lease_expires_at <= now,
            )
            .order_by(WorkflowRun.lease_expires_at)
            .limit(1)
        )
        if engine.dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)
        run = db.scalar(query)
        if run is None:
            return False
        error = RuntimeError("workflow lease expired after maximum attempts")
        lease_owner = str(run.lease_owner or "")
        mark_failed(run, now, error)
        _RUNTIME.transition_failure(
            run.workflow_type, db, run, now, error, False, worker_id=lease_owner
        )
        db.commit()
        return True


class _RetiredWorkflowDefinitionError(RuntimeError):
    """A persisted run references a step plan removed from the deployed runtime."""

    error_code = _RETIRED_DEFINITION_ERROR_CODE
    error_category = StepErrorCategory.VALIDATION.value
    step_name = "workflow_definition"

    def __init__(self, run: WorkflowRun) -> None:
        super().__init__(
            "workflow definition is retired; retry from the current workflow version"
        )
        self.workflow_type = str(run.workflow_type or "")
        self.definition_version = int(run.definition_version or 0)


def _cancel_unfinished_checkpoints(
    db, run: WorkflowRun, now: datetime, error: Exception,
) -> None:
    """Keep a retired run's timeline terminal instead of leaving its last step running."""
    cancellable = {
        StepStatus.PENDING.value,
        StepStatus.RUNNING.value,
        StepStatus.RETRY_WAIT.value,
        StepStatus.ACTIVITY_RETRY_WAIT.value,
        StepStatus.WAITING_EXTERNAL.value,
        StepStatus.BLOCKED.value,
    }
    for checkpoint in db.scalars(
        select(WorkflowStepCheckpoint)
        .where(WorkflowStepCheckpoint.workflow_run_id == run.workflow_run_id)
        .with_for_update()
    ):
        if checkpoint.status not in cancellable:
            continue
        ensure_step_transition(checkpoint.status, StepStatus.CANCELLED)
        checkpoint.status = StepStatus.CANCELLED.value
        checkpoint.completed_at = now
        checkpoint.next_attempt_at = None
        checkpoint.lease_owner = None
        checkpoint.lease_expires_at = None
        checkpoint.last_error_category = StepErrorCategory.VALIDATION.value
        checkpoint.last_error_code = _RETIRED_DEFINITION_ERROR_CODE
        checkpoint.last_error_message = str(error)[:2000]
        checkpoint.updated_at = now


def _retire_unsupported_definition(
    db,
    run: WorkflowRun,
    now: datetime,
) -> None:
    """Fail a known workflow type whose frozen plan no longer exists.

    The current transition handler owns the business projection, so this still gives
    the user the workflow's normal retry or repair action. We never run the old plan,
    and we never silently reinterpret its checkpoints as a new plan.
    """
    error = _RetiredWorkflowDefinitionError(run)
    _mark_claimed(run, _RETIRED_DEFINITION_WORKER, now, now)
    _cancel_unfinished_checkpoints(db, run, now, error)
    mark_failed(run, now, error)
    _RUNTIME.transition_failure(
        str(run.workflow_type),
        db,
        run,
        now,
        error,
        False,
        worker_id=_RETIRED_DEFINITION_WORKER,
        error_code=_RETIRED_DEFINITION_ERROR_CODE,
        error_category=StepErrorCategory.VALIDATION.value,
        step_name="workflow_definition",
    )


def _retire_one_unsupported_definition(now: datetime) -> bool:
    """Project one retired known workflow run into a user-recoverable terminal state.

    Unknown historical workflow types remain outside this deployment's ownership.
    This path deliberately handles only a currently registered business type with an
    obsolete definition version, because its current transition handler can safely
    publish the existing user-facing recovery action.
    """
    with SessionLocal() as db:
        query = (
            select(WorkflowRun)
            .where(
                WorkflowRun.workflow_type.in_(_supported_workflow_types()),
                WorkflowRun.definition_version > 0,
                _eligible(now),
            )
            .order_by(WorkflowRun.available_at, WorkflowRun.started_at)
            .limit(20)
        )
        if engine.dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)
        for run in db.scalars(query):
            if _supports_workflow_run(run):
                continue
            subject = _workflow_subject(run)
            if subject is None:
                continue
            _lock_workflow_subject(db, run)
            if db.scalar(_active_same_subject_query(run, now)) is not None:
                # PostgreSQL rollback invalidates the streamed SELECT result. The next
                # poll will rescan safely after the active run releases its subject.
                db.rollback()
                return False
            _retire_unsupported_definition(db, run, now)
            db.commit()
            log_event(
                logger,
                logging.WARNING,
                "workflow_definition_retired",
                workflow_run_id=run.workflow_run_id,
                workflow_type=run.workflow_type,
                definition_version=run.definition_version,
            )
            return True
        return False


def claim_next_workflow_run(worker_id: str, *, lease_seconds: int | None = None) -> str | None:
    """抢占一条可执行的 WorkflowRun，并给它设置租约。

    租约用于避免多个 worker 进程/线程同时处理同一条任务；同一个 subject
    也会被加锁，避免同一候选人、同一申请或同一文档并发执行互相覆盖状态。
    """
    lease_seconds = lease_seconds or settings.workflow_worker_lease_seconds
    now = _now()
    _finalize_one_exhausted_lease(now)
    _retire_one_unsupported_definition(now)
    lease_expires_at = now + timedelta(seconds=lease_seconds)
    with SessionLocal() as db:
        base = (
            select(WorkflowRun)
            .where(
                WorkflowRun.workflow_type.in_(_supported_workflow_types()),
                WorkflowRun.definition_version > 0,
                _eligible(now),
            )
            .order_by(WorkflowRun.available_at, WorkflowRun.started_at)
        )
        if engine.dialect.name == "postgresql":
            candidate_ids = list(
                db.scalars(base.with_only_columns(WorkflowRun.workflow_run_id).limit(20))
            )
            for run_id in candidate_ids:
                run = db.scalar(
                    select(WorkflowRun)
                    .where(WorkflowRun.workflow_run_id == run_id)
                    .with_for_update(skip_locked=True)
                )
                if run is None or not _run_is_eligible(run, now):
                    db.rollback()
                    continue
                _lock_workflow_subject(db, run)
                active_same_subject = db.scalar(_active_same_subject_query(run, now))
                if active_same_subject is not None:
                    db.rollback()
                    continue
                _mark_claimed(run, worker_id, now, lease_expires_at)
                db.commit()
                return run.workflow_run_id
            return None

        candidate_ids = list(db.scalars(base.with_only_columns(WorkflowRun.workflow_run_id).limit(10)))
        for run_id in candidate_ids:
            candidate = db.get(WorkflowRun, run_id)
            if candidate is None or not _run_is_eligible(candidate, now):
                continue
            active_same_subject = db.scalar(_active_same_subject_query(candidate, now))
            if active_same_subject is not None:
                continue
            claimed = db.execute(
                update(WorkflowRun)
                .where(
                    WorkflowRun.workflow_run_id == run_id,
                    _eligible(now),
                )
                .values(
                    status=WorkflowRunStatus.RUNNING.value,
                    attempt_count=WorkflowRun.attempt_count + 1,
                    lease_owner=worker_id,
                    lease_expires_at=lease_expires_at,
                    heartbeat_at=now,
                    updated_at=now,
                )
                .execution_options(synchronize_session=False)
            )
            if claimed.rowcount == 1:
                db.commit()
                return run_id
            db.rollback()
        return None


def _run_is_eligible(run: WorkflowRun, now: datetime) -> bool:
    # 只领取当前部署仍有精确 Step 定义的任务。旧编排由上方的退役投影处理，
    # 不允许先租约再在 execute() 中发现版本缺失。
    if not _supports_workflow_run(run):
        return False
    # 新步骤式 Workflow 的每步上限在 checkpoint 管理，不使用旧的整任务次数上限。
    if not _uses_step_recovery(run.workflow_type, run.definition_version) and run.attempt_count >= run.max_attempts:
        return False
    if run.status == WorkflowRunStatus.PENDING.value:
        return run.available_at is None or run.available_at <= now
    return (
        run.status == WorkflowRunStatus.RUNNING.value
        and run.lease_expires_at is not None
        and run.lease_expires_at <= now
    )


def _mark_claimed(
    run: WorkflowRun,
    worker_id: str,
    now: datetime,
    lease_expires_at: datetime,
) -> None:
    ensure_workflow_transition(run.status, WorkflowRunStatus.RUNNING)
    run.status = WorkflowRunStatus.RUNNING.value
    run.attempt_count += 1
    run.lease_owner = worker_id
    run.lease_expires_at = lease_expires_at
    run.heartbeat_at = now
    run.updated_at = now




def _heartbeat(stop: threading.Event, run_id: str, worker_id: str, lease_seconds: int) -> None:
    """定期续约；Worker 异常退出后停止续约，其他 Worker 才能接管该任务。"""
    interval = max(1.0, lease_seconds / 3)
    while not stop.wait(interval):
        now = _now()
        with SessionLocal() as db:
            renewed = db.execute(
                update(WorkflowRun)
                .where(
                    WorkflowRun.workflow_run_id == run_id,
                    WorkflowRun.status == WorkflowRunStatus.RUNNING.value,
                    WorkflowRun.lease_owner == worker_id,
                )
                .values(
                    heartbeat_at=now,
                    lease_expires_at=now + timedelta(seconds=lease_seconds),
                    updated_at=now,
                )
                .execution_options(synchronize_session=False)
            )
            db.commit()
            if renewed.rowcount != 1:
                return




def _is_non_retryable_failure(error: Exception) -> bool:
    explicit = retryable_decision(error)
    return explicit is False


def _record_failure(run_id: str, worker_id: str, _result: dict[str, Any] | None, error: Exception) -> None:
    """把步骤式运行时的未分类异常交给 StepRunner 做一次原子失败发布。

    当前步骤式 Workflow 的 Worker 只负责日志、租约和调用，不提交 WorkflowRun
    或领域对象的失败状态。仅无步骤的历史 Workflow 保留原有兼容收尾逻辑。
    """
    with SessionLocal() as db:
        run = db.scalar(
            select(WorkflowRun)
            .where(WorkflowRun.workflow_run_id == run_id)
            .with_for_update()
        )
        if run is None or run.status != WorkflowRunStatus.RUNNING.value or run.lease_owner != worker_id:
            return
        if not _supports_workflow_run(run):
            _retire_unsupported_definition(db, run, _now())
            db.commit()
            return
        if _uses_step_recovery(run.workflow_type):
            _RUNTIME.finalize_unhandled_failure(
                str(run.workflow_type),
                db=db,
                run=run,
                worker_id=worker_id,
                error=error,
            )
            metrics.increment("recruit_workflow_failures", workflow_type=run.workflow_type, retrying="false")
            log_event(
                logger,
                logging.ERROR,
                "workflow_unhandled_failure_finalized",
                workflow_run_id=run.workflow_run_id,
                workflow_type=run.workflow_type,
                error_type=type(error).__name__,
            )
            return

        # 兼容没有 StepDefinition 的历史任务；当前注册的正式工作流不会走到这里。
        now = _now()
        retrying = (
            run.attempt_count < run.max_attempts
            and not _is_non_retryable_failure(error)
        )
        if retrying:
            delay = min(120, 5 * (2 ** max(0, run.attempt_count - 1)))
            schedule_retry(run, now, error, available_at=now + timedelta(seconds=delay))
        else:
            mark_failed(run, now, error)
        _RUNTIME.transition_failure(
            run.workflow_type, db, run, now, error, retrying, worker_id=worker_id
        )
        db.commit()
def process_workflow_run(run_id: str, worker_id: str, *, lease_seconds: int | None = None) -> None:
    """执行一条已经被当前 worker 领取的 WorkflowRun。

    这里负责心跳、日志上下文和异常捕获；真正的业务流程由 WorkflowRuntime
    根据 workflow_type 分发到各模块 workflows/ 下的执行函数。
    """
    lease_seconds = lease_seconds or settings.workflow_worker_lease_seconds
    result: dict[str, Any] | None = None
    stop = threading.Event()
    heartbeat = threading.Thread(
        target=_heartbeat,
        args=(stop, run_id, worker_id, lease_seconds),
        name=f"workflow-heartbeat-{run_id}",
        daemon=True,
    )
    heartbeat.start()
    try:
        with SessionLocal() as db:
            run = db.get(WorkflowRun, run_id)
            if run is None or run.status != WorkflowRunStatus.RUNNING.value:
                return
            workflow_type = run.workflow_type
            application_id = run.application_id
            originating_request_id = run.request_id
        # 这层只绑定工作流级关联 ID，并记录领取、收尾等运行事实。下游 StepRunner
        # 会补充 step_name / external_request_id；所有 JSON stdout 因此可串成一条链路。
        with bind_context(request_id=originating_request_id, workflow_run_id=run_id,
                          application_id=application_id, worker_id=worker_id):
            # Worker 的开始事件服务于后台排障；前端的步骤进度由 StepRunner 落库事件读取。
            log_event(logger, logging.INFO, "workflow_started", workflow_type=workflow_type)
            plan_result = _RUNTIME.execute(workflow_type, run_id, worker_id)
            if plan_result.status.value == "failed":
                # StepRunner 已在同一短事务内提交检查点、WorkflowRun、领域状态与时间线。
                # Worker 这里只记录运行日志，严禁再次补写业务失败状态。
                log_event(
                    logger,
                    logging.ERROR,
                    "workflow_failed",
                    workflow_type=workflow_type,
                    step_name=plan_result.current_step_name,
                    error_category=(plan_result.error_category.value if plan_result.error_category else ""),
                    error_code=plan_result.error_code or "",
                )
            else:
                event = {
                    "completed": "workflow_completed",
                    "deferred": "workflow_deferred",
                    "blocked": "workflow_blocked",
                }[plan_result.status.value]
                log_event(
                    logger,
                    logging.INFO if plan_result.status.value == "completed" else logging.WARNING,
                    event,
                    workflow_type=workflow_type,
                    step_name=plan_result.current_step_name,
                    step_status=plan_result.current_step_status,
                    next_attempt_at=plan_result.next_attempt_at,
                    error_category=(plan_result.error_category.value if plan_result.error_category else ""),
                    error_code=plan_result.error_code or "",
                )
    except Exception as exc:
        # 未被 StepRunner 分类的兜底异常只写技术日志，并交给 _record_failure 做领域收尾。
        logger.exception("workflow_run_failed", extra={"event": "workflow_failed", "workflow_run_id": run_id})
        _record_failure(run_id, worker_id, result, exc)
    finally:
        stop.set()
        heartbeat.join(timeout=1)


def process_next_workflow_run(worker_id: str | None = None) -> bool:
    """领取并同步执行一条任务；供测试、单次执行和常驻轮询循环复用。"""
    worker_id = worker_id or _worker_id()
    run_id = claim_next_workflow_run(worker_id)
    if run_id is None:
        return False
    process_workflow_run(run_id, worker_id)
    return True


def run_forever(
    worker_id: str | None = None,
    *,
    concurrency: int | None = None,
) -> None:
    """生产 Worker 的常驻主循环：持续轮询队列，并以受限线程池并发执行任务。"""
    worker_id = worker_id or _worker_id()
    concurrency = max(1, concurrency or settings.workflow_worker_concurrency)
    logger.info(
        "workflow_worker_started worker_id=%s concurrency=%s",
        worker_id,
        concurrency,
    )
    futures: set[Future[None]] = set()
    with ThreadPoolExecutor(
        max_workers=concurrency,
        thread_name_prefix="workflow",
    ) as executor:
        while True:
            completed = {future for future in futures if future.done()}
            for future in completed:
                futures.remove(future)
                try:
                    future.result()
                except Exception:
                    logger.exception("workflow_executor_task_failed")

            claimed_any = False
            while len(futures) < concurrency:
                try:
                    run_id = claim_next_workflow_run(worker_id)
                except Exception:
                    logger.exception("workflow_claim_failed")
                    break
                if run_id is None:
                    break
                claimed_any = True
                futures.add(executor.submit(process_workflow_run, run_id, worker_id))

            if claimed_any:
                continue
            if futures:
                wait(
                    futures,
                    timeout=settings.workflow_worker_poll_seconds,
                    return_when=FIRST_COMPLETED,
                )
            else:
                time.sleep(settings.workflow_worker_poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Recruitment workflow worker")
    parser.add_argument("--once", action="store_true", help="Process at most one queued workflow")
    parser.add_argument(
        "--concurrency",
        type=int,
        default=settings.workflow_worker_concurrency,
        help="Maximum concurrent workflow runs in this worker process",
    )
    args = parser.parse_args()
    configure_logging()
    init_db()
    if args.once:
        process_next_workflow_run()
        return
    run_forever(concurrency=args.concurrency)


if __name__ == "__main__":
    main()
