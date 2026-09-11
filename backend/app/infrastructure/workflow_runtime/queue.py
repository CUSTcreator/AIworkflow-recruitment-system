"""持久化工作流的入队层。业务模块只能通过这里创建 WorkflowRun；本文件不执行具体业务。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.infrastructure.observability.context import request_id
from backend.app.models.entities import WorkflowRun
from backend.app.shared.workflows import WorkflowRunStatus


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12].upper()}"


def _sanitize(value: Any) -> Any:
    blocked = {"api_key", "embedding_api_key", "token", "secret", "password"}
    if isinstance(value, dict):
        return {
            str(key): _sanitize(item)
            for key, item in value.items()
            if str(key).lower() not in blocked
        }
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    return value


class WorkflowQueue:
    """持久化任务的唯一入队入口：负责去重和记录，不负责实际执行。"""

    def __init__(self, db: Session) -> None:
        self.db = db

    def enqueue(
        self,
        *,
        workflow_type: str,
        subject_type: str,
        subject_id: str,
        triggered_by: str,
        input_json: dict[str, Any] | None = None,
        application_id: str | None = None,
        max_attempts: int | None = None,
        definition_version: int = 1,
        reuse_active: bool = True,
    ) -> tuple[WorkflowRun, bool]:
        """创建一条持久化 WorkflowRun，或复用同 subject 的活跃任务。

        WorkflowQueue 只负责把任务写入数据库，不执行 Python 业务函数。
        独立 worker 后续会轮询 pending/running 过期任务，再根据 workflow_type
        通过 WorkflowRuntime 找到已注册的处理函数。
        """
        if reuse_active:
            active = self.active_run(
                workflow_type=workflow_type,
                subject_type=subject_type,
                subject_id=subject_id,
            )
            if active is not None:
                return active, False
        now = _now()
        run = WorkflowRun(
            workflow_run_id=_id("WR"),
            application_id=application_id,
            subject_type=subject_type,
            subject_id=subject_id,
            workflow_type=workflow_type,
            status=WorkflowRunStatus.PENDING.value,
            started_at=now,
            completed_at=None,
            triggered_by=triggered_by,
            input_json=_sanitize(input_json or {}),
            request_id=request_id(),
            available_at=now,
            attempt_count=0,
            max_attempts=max_attempts or settings.workflow_worker_max_attempts,
            definition_version=definition_version,
            updated_at=now,
        )
        self.db.add(run)
        return run, True

    def retry(
        self, original: WorkflowRun, *, triggered_by: str, reason: str,
        definition_version: int | None = None,
    ) -> WorkflowRun:
        """基于已结束任务新建一次重试。

        默认沿用原定义；当业务主动提供新版本时，重试会在同一输入快照下采用
        已注册的新编排，避免已退役定义导致历史失败任务无法恢复。
        """
        subject_type, subject_id = self.subject_for(original)
        run, created = self.enqueue(
            workflow_type=original.workflow_type,
            subject_type=subject_type,
            subject_id=subject_id,
            application_id=original.application_id,
            triggered_by=triggered_by,
            max_attempts=original.max_attempts,
            definition_version=(definition_version or original.definition_version),
            input_json={
                **(original.input_json or {}),
                "retry_of": original.workflow_run_id,
                "retry_reason": reason,
            },
        )
        if not created:
            raise RuntimeError("workflow_active_run_exists")
        return run

    def active_run(
        self,
        *,
        workflow_type: str,
        subject_type: str,
        subject_id: str,
    ) -> WorkflowRun | None:
        """查找同一业务对象上仍在排队或执行的同类型任务，用于幂等入队。"""
        return self.db.scalar(
            select(WorkflowRun)
            .where(
                WorkflowRun.workflow_type == workflow_type,
                WorkflowRun.subject_type == subject_type,
                WorkflowRun.subject_id == subject_id,
                WorkflowRun.status.in_((WorkflowRunStatus.PENDING.value, WorkflowRunStatus.RUNNING.value)),
            )
            .order_by(WorkflowRun.started_at.desc(), WorkflowRun.workflow_run_id.desc())
        )

    @staticmethod
    def subject_for(run: WorkflowRun) -> tuple[str, str]:
        """返回任务互斥锁所使用的业务对象；兼容仅保存 application_id 的历史任务。"""
        if run.subject_type and run.subject_id:
            return run.subject_type, run.subject_id
        if run.application_id:
            return "application", run.application_id
        raise ValueError("workflow_subject_missing")

