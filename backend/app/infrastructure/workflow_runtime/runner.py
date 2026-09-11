"""Workflow 外层运行器：按冻结的定义版本把任务交给 StepRunner。"""
from __future__ import annotations

from backend.app.db.session import SessionLocal
from backend.app.infrastructure.workflow_runtime.step_runner import StepRunner
from backend.app.models.entities import WorkflowRun
from backend.app.shared.workflows import RunPlanResult, WorkflowContext, WorkflowRegistry


class WorkflowRunner:
    def __init__(self, registry: WorkflowRegistry, step_runner: StepRunner | None = None) -> None:
        self.registry = registry
        self.step_runner = step_runner or StepRunner()

    def execute(self, workflow_type: str, context: WorkflowContext) -> RunPlanResult:
        """返回明确的计划结果，供 Worker、API 投影和运维日志统一使用。"""
        with SessionLocal() as db:
            run = db.get(WorkflowRun, context.workflow_run_id)
            if run is None:
                raise RuntimeError("workflow_run_not_found")
            definition_version = run.definition_version
        spec = self.registry.require(workflow_type, definition_version)
        if not spec.steps:
            raise RuntimeError(f"workflow_steps_required:{workflow_type}")
        return self.step_runner.run_plan(spec, context)

    def finalize_unhandled_failure(
        self,
        workflow_type: str,
        *,
        db: object,
        run: WorkflowRun,
        worker_id: str,
        error: Exception,
    ) -> None:
        """把步骤框架之外的异常交回 StepRunner 以同一事务语义发布。"""
        spec = self.registry.require(workflow_type, run.definition_version)
        self.step_runner.finalize_unhandled_failure(
            workflow=spec,
            db=db,
            run=run,
            worker_id=worker_id,
            error=error,
        )