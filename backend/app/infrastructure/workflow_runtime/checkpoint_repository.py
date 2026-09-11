"""步骤检查点的持久化操作。

WorkflowRun 的租约仍是并发所有权的唯一来源；每一次写检查点前都校验该租约，
避免已失去任务所有权的旧 Worker 覆盖新 Worker 的恢复结果。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.entities import WorkflowRun, WorkflowStepCheckpoint
from backend.app.shared.workflows import StepDefinition, StepStatus, WorkflowRunStatus
from backend.app.shared.workflows.status_contracts import ensure_step_transition


def now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def stable_hash(value: dict[str, Any]) -> str:
    import json
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


class CheckpointRepository:
    """检查点读写仓储；不调用外部服务，也不决定具体业务产物。"""

    def require_owned_run(self, db: Session, run_id: str, worker_id: str) -> WorkflowRun:
        run = db.scalar(select(WorkflowRun).where(WorkflowRun.workflow_run_id == run_id).with_for_update())
        if run is None or run.status != WorkflowRunStatus.RUNNING.value or run.lease_owner != worker_id:
            raise RuntimeError("workflow_lease_lost")
        return run

    def get_or_start(self, db: Session, *, run_id: str, worker_id: str, definition: StepDefinition, definition_version: int, input_hash: str) -> WorkflowStepCheckpoint:
        self.require_owned_run(db, run_id, worker_id)
        checkpoint = db.scalar(select(WorkflowStepCheckpoint).where(WorkflowStepCheckpoint.workflow_run_id == run_id, WorkflowStepCheckpoint.step_name == definition.name).with_for_update())
        if checkpoint is None:
            checkpoint = WorkflowStepCheckpoint(
                checkpoint_id=f"WSC_{uuid4().hex}", workflow_run_id=run_id, step_name=definition.name,
                step_order=definition.order, definition_version=definition_version, status=StepStatus.PENDING.value,
                input_hash=input_hash, idempotency_key=sha256(f"{run_id}:{definition.name}:{input_hash}".encode()).hexdigest(), external_request_id=sha256(f"external:{run_id}:{definition.name}:{input_hash}".encode()).hexdigest(),
                output_refs_json={}, deadline_at=now() + timedelta(seconds=definition.policy.total_deadline_seconds),
                max_attempts_snapshot=definition.policy.max_attempts,
                max_poll_attempts_snapshot=definition.policy.max_poll_attempts,
            )
            db.add(checkpoint)
            db.flush()
        if checkpoint.input_hash != input_hash:
            raise RuntimeError(f"workflow_step_input_changed:{definition.name}")
        if checkpoint.status == StepStatus.SUCCEEDED.value:
            return checkpoint
        # 异步任务的“继续轮询”不等于“重新发起一次外部请求”。前者只计入
        # poll_count，后者（pending/retry_wait/崩溃恢复）才消耗 attempt_count。
        is_external_poll = checkpoint.status == StepStatus.WAITING_EXTERNAL.value
        # 子活动的退避恢复只重新进入同一业务 Step；已成功子活动会从自己的
        # 检查点复用，因此它不能消耗整个 Step 的 max_attempts。
        is_activity_resume = checkpoint.status == StepStatus.ACTIVITY_RETRY_WAIT.value
        ensure_step_transition(checkpoint.status, StepStatus.RUNNING)
        checkpoint.lease_owner = worker_id
        checkpoint.lease_expires_at = now() + timedelta(seconds=definition.policy.timeout_seconds + 30)
        checkpoint.status = StepStatus.RUNNING.value
        if is_external_poll:
            checkpoint.poll_count += 1
        elif not is_activity_resume:
            checkpoint.attempt_count += 1
        checkpoint.started_at = now()
        checkpoint.updated_at = now()
        db.flush()
        return checkpoint

    def invalidate_from(self, db: Session, *, run_id: str, from_order: int) -> None:
        """输入版本变化时使当前及后续步骤的旧结果失效，保留审计行而不删除。"""
        for row in db.scalars(select(WorkflowStepCheckpoint).where(WorkflowStepCheckpoint.workflow_run_id == run_id, WorkflowStepCheckpoint.step_order >= from_order).with_for_update()):
            ensure_step_transition(row.status, StepStatus.INVALIDATED)
            row.status = StepStatus.INVALIDATED.value
            row.output_refs_json = {}
            row.next_attempt_at = None
            row.completed_at = now()
            row.lease_owner = None
            row.lease_expires_at = None
            row.updated_at = now()

    def completed_step_refs(self, db: Session, *, run_id: str, before_order: int) -> dict[str, dict[str, Any]]:
        """汇总此前已成功步骤的输出引用，供后续步骤从 Artifact 恢复输入。"""
        rows = db.scalars(select(WorkflowStepCheckpoint).where(
            WorkflowStepCheckpoint.workflow_run_id == run_id,
            WorkflowStepCheckpoint.step_order < before_order,
            WorkflowStepCheckpoint.status == StepStatus.SUCCEEDED.value,
        ).order_by(WorkflowStepCheckpoint.step_order)).all()
        return {row.step_name: dict(row.output_refs_json or {}) for row in rows}

    def current_for_run(self, db: Session, workflow_run_id: str) -> WorkflowStepCheckpoint | None:
        """锁定并返回当前检查点，供步骤框架外的失败收尾使用。"""
        checkpoints = list(db.scalars(
            select(WorkflowStepCheckpoint)
            .where(WorkflowStepCheckpoint.workflow_run_id == workflow_run_id)
            .order_by(WorkflowStepCheckpoint.step_order)
            .with_for_update()
        ))
        if not checkpoints:
            return None
        terminal = {
            StepStatus.SUCCEEDED.value,
            StepStatus.FAILED.value,
            StepStatus.BLOCKED.value,
            StepStatus.CANCELLED.value,
            StepStatus.INVALIDATED.value,
        }
        return next((item for item in checkpoints if item.status not in terminal), None)

    def succeed(self, checkpoint: WorkflowStepCheckpoint, refs: dict[str, Any]) -> None:
        ensure_step_transition(checkpoint.status, StepStatus.SUCCEEDED)
        checkpoint.status = StepStatus.SUCCEEDED.value
        checkpoint.output_refs_json = dict(refs)
        checkpoint.last_error_category = None
        checkpoint.last_error_code = None
        checkpoint.last_error_message = None
        checkpoint.next_attempt_at = None
        checkpoint.lease_owner = None
        checkpoint.lease_expires_at = None
        checkpoint.completed_at = now()
        checkpoint.updated_at = now()

    def defer(self, checkpoint: WorkflowStepCheckpoint, *, status: StepStatus, after_seconds: int, error_category: str | None = None, error_code: str | None = None, error_message: str | None = None, external_job_id: str | None = None) -> datetime:
        if status not in {StepStatus.RETRY_WAIT, StepStatus.ACTIVITY_RETRY_WAIT, StepStatus.WAITING_EXTERNAL}:
            raise ValueError("workflow_step_defer_status_invalid")
        available_at = now() + timedelta(seconds=max(1, after_seconds))
        ensure_step_transition(checkpoint.status, status)
        checkpoint.status = status.value
        checkpoint.next_attempt_at = available_at
        checkpoint.external_job_id = external_job_id or checkpoint.external_job_id
        checkpoint.last_error_category = error_category
        checkpoint.last_error_code = error_code
        checkpoint.last_error_message = (error_message or "")[:2000] or None
        # 延迟后归还步骤租约。下一次 Worker 必须重新领取，不能沿用本次执行权。
        checkpoint.lease_owner = None
        checkpoint.lease_expires_at = None
        checkpoint.updated_at = now()
        return available_at

    def fail(self, checkpoint: WorkflowStepCheckpoint, *, error_code: str, error_message: str, error_category: str | None = None) -> None:
        ensure_step_transition(checkpoint.status, StepStatus.FAILED)
        checkpoint.status = StepStatus.FAILED.value
        checkpoint.last_error_category = error_category
        checkpoint.last_error_code = error_code
        checkpoint.last_error_message = error_message[:2000]
        checkpoint.lease_owner = None
        checkpoint.lease_expires_at = None
        checkpoint.completed_at = now()
        checkpoint.updated_at = now()



