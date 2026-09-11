"""工作流运行时：维护已注册类型，并把 Worker 领取的任务分发给对应业务处理器。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from backend.app.shared.workflows import (
    RunPlanResult,
    WorkflowContext,
    WorkflowRegistry,
    WorkflowSpec,
    WorkflowTransitionContext,
)

from .runner import WorkflowRunner


WorkflowSubjectLocker = Callable[[Any, str], None]


class WorkflowRuntime:
    """Worker 使用的分发器：管理类型注册、执行分发、失败收尾和业务对象锁。"""

    def __init__(self) -> None:
        self.registry = WorkflowRegistry()
        self.runner = WorkflowRunner(self.registry)
        self._subject_lockers: dict[str, WorkflowSubjectLocker] = {}

    def register(self, spec: WorkflowSpec) -> None:
        """登记一个可持久化执行的业务 Workflow。"""
        self.registry.register(spec)

    def register_subject_locker(
        self,
        subject_type: str,
        locker: WorkflowSubjectLocker,
    ) -> None:
        if not subject_type.strip():
            raise ValueError("workflow_subject_type_missing")
        current = self._subject_lockers.get(subject_type)
        if current is not None and current is not locker:
            raise RuntimeError(f"duplicate_workflow_subject_locker:{subject_type}")
        self._subject_lockers[subject_type] = locker

    def supported_types(self) -> frozenset[str]:
        return self.registry.types()

    def supports(self, workflow_type: str | None, definition_version: int | None) -> bool:
        """Whether this deployment can execute a persisted type/version pair."""
        return self.registry.supports(workflow_type, definition_version)

    def execute(self, workflow_type: str | None, run_id: str, worker_id: str) -> RunPlanResult:
        """按类型构造最小执行上下文，并返回本次计划执行的明确状态。"""
        spec = self.registry.require(workflow_type)
        return self.runner.execute(
            spec.workflow_type,
            WorkflowContext(workflow_run_id=run_id, worker_id=worker_id),
        )

    def finalize_unhandled_failure(
        self,
        workflow_type: str,
        *,
        db: Any,
        run: Any,
        worker_id: str,
        error: Exception,
    ) -> None:
        """运行时兜底异常统一委派给 StepRunner，Worker 不拥有提交权。"""
        self.runner.finalize_unhandled_failure(
            workflow_type,
            db=db,
            run=run,
            worker_id=worker_id,
            error=error,
        )

    def transition_failure(
        self,
        workflow_type: str,
        db: Any,
        run: Any,
        now: datetime,
        error: Exception,
        retrying: bool,
        *,
        worker_id: str = "",
        error_code: str = "",
        error_category: str = "",
        step_name: str = "",
        recovery_action: str | None = None,
    ) -> None:
        """在重试或最终失败时调用模块提供的收尾处理器，修复业务侧状态。"""
        spec = self.registry.require(workflow_type)
        if spec.transition_handler is None:
            return
        spec.transition_handler(
            WorkflowTransitionContext(
                workflow_run_id=run.workflow_run_id,
                worker_id=worker_id,
                error=error,
                retrying=retrying,
                recovery_action=recovery_action,
                metadata={
                    "db": db,
                    "run": run,
                    "now": now,
                    "error_code": error_code,
                    "error_category": error_category,
                    "step_name": step_name,
                },
            )
        )

    def lock_subject(self, db: Any, subject_type: str, subject_id: str) -> None:
        """锁定任务影响的业务对象，防止同一申请、文档或提交记录被并发处理。"""
        locker = self._subject_lockers.get(subject_type)
        if locker is None:
            raise RuntimeError(f"unsupported_workflow_subject:{subject_type}")
        locker(db, subject_id)
