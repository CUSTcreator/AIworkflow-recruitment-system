"""步骤级恢复执行器：统一执行 Step 的前置、业务调用、后置和重试。"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from random import uniform
from typing import Any

from backend.app.db.session import SessionLocal
from backend.app.infrastructure.observability.event_contracts import (
    ExecutionEventType,
    ExecutionSeverity,
)
from backend.app.infrastructure.observability.execution_event_writer import (
    WorkflowExecutionEventWriter,
)
from backend.app.infrastructure.observability.logging import get_logger, log_event
from backend.app.infrastructure.observability.persistence_errors import describe_persistence_error
from backend.app.infrastructure.observability.metrics import metrics
from backend.app.infrastructure.workflow_runtime.artifact_store import (
    WorkflowArtifactStore,
    artifact_publish_scope,
)
from backend.app.infrastructure.workflow_runtime.checkpoint_repository import CheckpointRepository, stable_hash
from backend.app.infrastructure.workflow_runtime.external_activity import (
    ExternalActivityContext,
    ExternalActivityEvent,
    external_activity_scope,
)
from backend.app.infrastructure.workflow_runtime.lifecycle import mark_blocked, mark_completed, mark_failed, schedule_retry
from backend.app.models.entities import WorkflowRun, WorkflowStepCheckpoint
from backend.app.shared.errors import BusinessError, ExternalServiceError, retryable_decision
from backend.app.shared.workflows import (
    RecoveryAction,
    RunPlanResult,
    RunPlanStatus,
    StepContext,
    StepDefinition,
    StepErrorCategory,
    StepOutcome,
    StepOutcomeKind,
    StepStatus,
    WorkflowContext,
    WorkflowSpec,
    WorkflowTransitionContext,
)
from recruitment_ai_core.execution import execution_timeout_budget, model_call_scope

logger = get_logger(__name__)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class StepTerminalFailure(RuntimeError):
    """当前 Step 已在自己的事务内完成失败收尾；Worker 只记录运行日志。"""

    def __init__(
        self,
        step_name: str,
        error_code: str,
        error_message: str,
        error_category: StepErrorCategory = StepErrorCategory.INTERNAL,
        recovery_action: RecoveryAction | None = None,
    ) -> None:
        super().__init__(f"workflow_step_failed:{step_name}:{error_code}")
        self.step_name = step_name
        self.error_code = error_code
        self.error_message = error_message
        self.error_category = error_category
        self.recovery_action = recovery_action


class StepBlocked(RuntimeError):
    """Step 主动停止于用户可处理的业务阻塞，而非技术终态失败。"""

    def __init__(self, error_code: str, error_message: str) -> None:
        super().__init__(error_message)
        self.error_code = error_code


@dataclass(frozen=True, slots=True)
class StepRunResult:
    """单步执行的内部结果；RunPlanResult 是向 Worker 暴露的工作流级结果。"""

    status: str
    output_refs: dict[str, Any]
    step_status: str = ""
    next_attempt_at: datetime | None = None
    error_category: StepErrorCategory | None = None
    error_code: str | None = None
    recovery_action: RecoveryAction | None = None


class StepRunner:
    """所有步骤共享的可靠性切面；业务 Workflow 不得自行实现循环重试。"""

    def __init__(self, repository: CheckpointRepository | None = None) -> None:
        self.repository = repository or CheckpointRepository()
        # 写入器不提交事务，确保事件与步骤状态/领域产物原子可见。
        self.execution_events = WorkflowExecutionEventWriter()

    def _append_external_activity_events(
        self,
        db: Any,
        *,
        run: WorkflowRun,
        checkpoint: WorkflowStepCheckpoint,
        events: list[ExternalActivityEvent],
    ) -> None:
        """将外部适配器报告的事实与本 Step 的检查点原子发布。"""
        for event in events:
            self.execution_events.append(
                db,
                run=run,
                checkpoint=checkpoint,
                event_type=event.event_type,
                severity=(
                    ExecutionSeverity.ERROR
                    if event.event_type == ExecutionEventType.EXTERNAL_ACTIVITY_FAILED
                    else ExecutionSeverity.INFO
                ),
                error_category=event.error_category,
                error_code=event.error_code,
                diagnostics={
                    "service": event.service, "operation": event.operation,
                    "activity_type": str(event.activity_type), "activity_key": event.activity_key,
                    "duration_ms": event.duration_ms, "retryable": event.retryable,
                    "degraded": event.degraded, "external_request_id": event.external_request_id,
                    **event.diagnostics,
                },
            )

    def _project_transition(
        self,
        *,
        workflow: WorkflowSpec,
        context: WorkflowContext,
        db: Any,
        run: WorkflowRun,
        now: datetime,
        error: Exception,
        retrying: bool,
        error_code: str | None = None,
        error_category: StepErrorCategory | None = None,
        step_name: str | None = None,
        recovery_action: RecoveryAction | None = None,
    ) -> None:
        """在当前 Step 事务中投影领域状态，禁止 Worker 事后补写。"""
        if workflow.transition_handler is None:
            return
        workflow.transition_handler(
            WorkflowTransitionContext(
                workflow_run_id=run.workflow_run_id,
                worker_id=context.worker_id,
                error=error,
                retrying=retrying,
                recovery_action=recovery_action.value if isinstance(recovery_action, RecoveryAction) else recovery_action,
                metadata={
                    "db": db,
                    "run": run,
                    "now": now,
                    "error_code": error_code or str(getattr(error, "error_code", "") or ""),
                    "error_category": (
                        error_category.value if isinstance(error_category, StepErrorCategory) else ""
                    ),
                    "step_name": step_name or str(getattr(error, "step_name", "") or ""),
                },
            )
        )

    def _project_blocked_transition(
        self,
        *,
        workflow: WorkflowSpec,
        context: WorkflowContext,
        db: Any,
        run: WorkflowRun,
        now: datetime,
        error_code: str,
        error_message: str,
        recovery_action: RecoveryAction | None = None,
        step_name: str | None = None,
    ) -> None:
        """在同一短事务投影业务阻塞，不能把待确认误写成失败。"""
        if workflow.blocked_handler is None:
            return
        workflow.blocked_handler(
            WorkflowTransitionContext(
                workflow_run_id=run.workflow_run_id,
                worker_id=context.worker_id,
                error=StepBlocked(error_code, error_message),
                retrying=False,
                recovery_action=(recovery_action or RecoveryAction.REVIEW_REQUIRED).value,
                metadata={"db": db, "run": run, "now": now, "error_code": error_code, "error_message": error_message, "step_name": step_name},
            )
        )

    def _finalize_workflow_failure(
        self,
        *,
        workflow: WorkflowSpec,
        context: WorkflowContext,
        db: Any,
        run: WorkflowRun,
        checkpoint: WorkflowStepCheckpoint,
        error: Exception,
        error_category: StepErrorCategory,
        error_code: str,
        recovery_action: RecoveryAction | None = None,
    ) -> None:
        """原子发布终态失败：Step、Workflow、领域状态和时间线一起提交。"""
        now = _now()
        mark_failed(run, now, error)
        self._project_transition(
            workflow=workflow, context=context, db=db, run=run, now=now,
            error=error, retrying=False, error_code=error_code,
            error_category=error_category, step_name=checkpoint.step_name,
            recovery_action=recovery_action,
        )
        self.execution_events.append(
            db, run=run, checkpoint=checkpoint,
            event_type=ExecutionEventType.WORKFLOW_FAILED,
            severity=ExecutionSeverity.ERROR,
            error_category=error_category.value,
            error_code=error_code,
        )

    def finalize_unhandled_failure(
        self,
        *,
        workflow: WorkflowSpec,
        db: Any,
        run: WorkflowRun,
        worker_id: str,
        error: Exception,
    ) -> None:
        """收口未分类异常的失败发布。

        该入口只由运行时兜底调用。即使异常发生在步骤框架之外，WorkflowRun、
        领域状态与执行时间线仍必须在这一个事务中落库；Worker 不得自行补写。
        """
        # 框架外异常仍要尽量归属到当前检查点，否则领域状态机只能看到一条
        # 没有步骤和错误码的未知失败，无法给出正确的用户恢复动作。
        checkpoint = self.repository.current_for_run(db, run.workflow_run_id)
        error_code = f"workflow_unhandled:{type(error).__name__}"
        error_category = StepErrorCategory.INTERNAL
        if checkpoint is not None:
            self.repository.fail(
                checkpoint,
                error_code=error_code,
                error_message=str(error)[:2000],
                error_category=error_category.value,
            )
        now = _now()
        mark_failed(run, now, error)
        self._project_transition(
            workflow=workflow,
            context=WorkflowContext(workflow_run_id=run.workflow_run_id, worker_id=worker_id),
            db=db,
            run=run,
            now=now,
            error=error,
            retrying=False,
            error_code=error_code,
            error_category=error_category,
            step_name=checkpoint.step_name if checkpoint is not None else None,
        )
        self.execution_events.append(
            db,
            run=run,
            event_type=ExecutionEventType.WORKFLOW_FAILED,
            severity=ExecutionSeverity.ERROR,
            checkpoint=checkpoint,
            error_category=error_category.value,
            error_code=error_code,
        )
        # 与每个正常 Step 的发布边界一致：兜底失败也由 StepRunner 自己提交。
        db.commit()

    def run(self, *, workflow: WorkflowSpec, definition: StepDefinition, context: WorkflowContext) -> StepRunResult:
        """执行一个步骤。前置和后置均由本方法统一管理。

        本方法同时产生两条不同的数据流：``log_event`` 写技术 JSON stdout；
        ``execution_events.append`` 与检查点/领域产物同事务写执行时间线。两者
        不能互相替代，且时间线只保存稳定状态和错误码。
        """
        input_hash = stable_hash(definition.input_factory(context.workflow_run_id))
        with SessionLocal() as db:
            checkpoint = self.repository.get_or_start(
                db, run_id=context.workflow_run_id, worker_id=context.worker_id,
                definition=definition, definition_version=workflow.definition_version,
                input_hash=input_hash,
            )
            if checkpoint.status == StepStatus.SUCCEEDED.value:
                refs = dict(checkpoint.output_refs_json or {})
                db.commit()
                log_event(logger, 20, "workflow_step_reused", workflow_run_id=context.workflow_run_id, step_name=definition.name)
                return StepRunResult("succeeded", refs)
            attempt_deadline_at = min(
                checkpoint.deadline_at or (_now() + timedelta(seconds=definition.policy.timeout_seconds)),
                _now() + timedelta(seconds=definition.policy.timeout_seconds),
            )
            step_context = StepContext(
                workflow_run_id=context.workflow_run_id, worker_id=context.worker_id,
                step_name=definition.name, input_hash=input_hash,
                idempotency_key=checkpoint.idempotency_key,
                external_request_id=checkpoint.external_request_id or checkpoint.idempotency_key,
                attempt_number=checkpoint.attempt_count,
                max_attempts=checkpoint.max_attempts_snapshot or definition.policy.max_attempts,
                poll_number=checkpoint.poll_count,
                max_poll_attempts=(
                    checkpoint.max_poll_attempts_snapshot
                    or definition.policy.max_poll_attempts
                ),
                external_job_id=checkpoint.external_job_id,
                attempt_deadline_at=attempt_deadline_at,
                total_deadline_at=checkpoint.deadline_at,
                previous_output_refs=self.repository.completed_step_refs(
                    db, run_id=context.workflow_run_id, before_order=definition.order,
                ),
            )
            run = db.get(WorkflowRun, context.workflow_run_id)
            if run is None:
                raise RuntimeError("workflow_run_not_found")
            # “开始执行”与 checkpoint.running 一起提交；进程崩溃后页面不会看到
            # 一个不存在对应检查点的开始事件。
            self.execution_events.append(
                db, run=run, checkpoint=checkpoint,
                event_type=ExecutionEventType.STEP_STARTED,
            )
            db.commit()

        # 业务 handler 在事务外运行；外部客户端须读取 StepContext 的剩余预算。
        external_events: list[ExternalActivityEvent] = []
        try:
            log_event(logger, 20, "workflow_step_started", workflow_run_id=context.workflow_run_id, step_name=definition.name, timeout_seconds=step_context.remaining_timeout_seconds())
            with metrics.measure("recruit_workflow_step", workflow_type=workflow.workflow_type, step_name=definition.name):
                # 所有下游适配器都可读取同一份 Step 上下文；显式的业务 scope 可以在
                # 内层细分操作名，但不必重复传递根请求 ID、预算或幂等语义。
                activity_context = ExternalActivityContext(
                    workflow_run_id=step_context.workflow_run_id,
                    step_name=step_context.step_name,
                    attempt_number=step_context.attempt_number,
                    idempotency_key=step_context.idempotency_key,
                    external_request_id=step_context.external_request_id,
                    timeout_seconds=step_context.remaining_timeout_seconds(),
                    operation=step_context.step_name,
                )
                with external_activity_scope(activity_context) as captured_external_events:
                    external_events = captured_external_events
                    with model_call_scope(
                        step_context.external_request_id, step_context.step_name
                    ):
                        # 算法包不依赖后端 StepContext；通过运行时上下文读取相同的剩余预算。
                        with execution_timeout_budget(step_context.remaining_timeout_seconds()):
                            outcome = definition.handler(step_context)
            if _now() >= step_context.attempt_deadline_at:
                outcome = self._retry_outcome(definition, "step_attempt_timeout", "步骤执行超过本次时间预算")
            elif outcome.kind == StepOutcomeKind.SUCCEEDED and definition.artifact_type:
                # 阶段产物可能溢出到对象存储；该外部 I/O 必须在 persist_success 的
                # SQL 事务之外完成，之后只把暂存引用传给发布函数。
                staged = WorkflowArtifactStore().stage_outcome(
                    workflow_run_id=step_context.workflow_run_id,
                    step_name=definition.name,
                    artifact_type=definition.artifact_type,
                    payload=outcome.data,
                )
                outcome = replace(
                    outcome,
                    output_refs=WorkflowArtifactStore.attach_staged_output(
                        dict(outcome.output_refs), staged
                    ),
                )
        except ExternalServiceError as error:
            outcome = self._external_outcome(definition, error)
        except (TimeoutError, ConnectionError, OSError) as error:
            outcome = self._retry_outcome(definition, f"transport:{type(error).__name__}", str(error), StepErrorCategory.INFRASTRUCTURE)
        except BusinessError as error:
            outcome = StepOutcome.failed(
                error_code=error.code,
                error_message=error.message,
                error_category=StepErrorCategory.BUSINESS_RULE,
            )
        except Exception as error:
            # 算法包的并行聚合错误不依赖后端异常类型；若它明确标记为可恢复，
            # 仍必须回到当前检查点的 retry_wait 分支，而不能降级为内部终态失败。
            if retryable_decision(error) is True:
                outcome = self._retry_outcome(
                    definition,
                    f"external:{type(error).__name__}",
                    str(error),
                    StepErrorCategory.EXTERNAL_TRANSIENT,
                    retry_after_seconds=getattr(error, "retry_after_seconds", None),
                )
                return self._finalize(workflow, definition, context, step_context, outcome, external_events)
            log_event(logger, 40, "workflow_step_handler_error", workflow_run_id=context.workflow_run_id, step_name=definition.name, error_type=type(error).__name__, error_message=str(error))
            outcome = StepOutcome.failed(
                error_code=type(error).__name__,
                error_message=str(error),
                error_category=StepErrorCategory.INTERNAL,
            )
        return self._finalize(workflow, definition, context, step_context, outcome, external_events)

    def _finalize(self, workflow: WorkflowSpec, definition: StepDefinition, context: WorkflowContext, step_context: StepContext, outcome: StepOutcome, external_events: list[ExternalActivityEvent] | None = None) -> StepRunResult:
        """统一后置：成功发布、延迟重试、阻塞或最终失败。"""
        with SessionLocal() as db:
            run = self.repository.require_owned_run(db, context.workflow_run_id, context.worker_id)
            checkpoint = db.query(WorkflowStepCheckpoint).filter(
                WorkflowStepCheckpoint.workflow_run_id == context.workflow_run_id,
                WorkflowStepCheckpoint.step_name == definition.name,
            ).with_for_update().one()
            # 外部活动仅报告事实；此处才与检查点和最终 Step 状态一起提交。
            self._append_external_activity_events(
                db,
                run=run,
                checkpoint=checkpoint,
                events=external_events or [],
            )
            if outcome.kind == StepOutcomeKind.SUCCEEDED:
                if _now() >= step_context.attempt_deadline_at:
                    return self._defer(db, workflow, definition, context, run, checkpoint, self._retry_outcome(definition, "step_attempt_timeout", "发布前已超过本次时间预算"))
                try:
                    refs = dict(outcome.output_refs)
                    if definition.persist_success is not None:
                        # 正式领域产物与 checkpoint.success 在同一短事务提交。
                        with artifact_publish_scope():
                            refs = definition.persist_success(db, step_context, outcome)
                    self.repository.succeed(checkpoint, refs)
                    # 发布成功后才写“步骤成功”，确保前端事件不早于正式业务数据。
                    self.execution_events.append(
                        db, run=run, checkpoint=checkpoint,
                        event_type=ExecutionEventType.STEP_SUCCEEDED,
                    )
                    metrics.increment("recruit_workflow_step_success", workflow_type=workflow.workflow_type, step_name=definition.name)
                    log_event(logger, 20, "workflow_step_succeeded", workflow_run_id=context.workflow_run_id, step_name=definition.name)
                    db.commit()
                    return StepRunResult("succeeded", refs, step_status=StepStatus.SUCCEEDED.value)
                except ExternalServiceError as error:
                    # 发布阶段也可能涉及对象存储等外部适配器；回滚后仍只重试当前 Step。
                    db.rollback()
                    return self._finalize(
                        workflow, definition, context, step_context,
                        self._external_outcome(definition, error),
                    )
                except (TimeoutError, ConnectionError, OSError) as error:
                    db.rollback()
                    return self._finalize(
                        workflow, definition, context, step_context,
                        self._retry_outcome(definition, f"publish_transport:{type(error).__name__}", str(error)),
                    )
                except BusinessError as error:
                    db.rollback()
                    return self._finalize(
                        workflow, definition, context, step_context,
                        StepOutcome.failed(
                            error_code=error.code,
                            error_message=error.message,
                            error_category=StepErrorCategory.BUSINESS_RULE,
                        ),
                    )
                except Exception as error:
                    # 领域发布异常须被当前检查点记录为终态失败，不能留下 running 脏状态。
                    # 数据库异常只投影 SQLSTATE/约束名等脱敏元数据，绝不记录 SQL 参数。
                    details = describe_persistence_error(error)
                    log_event(
                        logger, 40, "workflow_publish_error",
                        workflow_run_id=context.workflow_run_id, step_name=definition.name,
                        error_type=type(error).__name__, **details,
                    )
                    db.rollback()
                    diagnostic = " / ".join(
                        item for item in (details.get("sqlstate"), details.get("constraint_name"), details.get("table_name")) if item
                    )
                    message = str(error) if not diagnostic else f"数据库发布约束异常：{diagnostic}"
                    return self._finalize(
                        workflow, definition, context, step_context,
                        StepOutcome.failed(error_code=f"publish:{type(error).__name__}", error_message=message),
                    )
            if outcome.kind == StepOutcomeKind.BLOCKED:
                now = _now()
                from backend.app.shared.workflows.status_contracts import ensure_step_transition
                ensure_step_transition(checkpoint.status, StepStatus.BLOCKED)
                checkpoint.status = StepStatus.BLOCKED.value
                checkpoint.last_error_category = outcome.error_category.value if outcome.error_category else None
                checkpoint.last_error_code = outcome.error_code
                checkpoint.last_error_message = outcome.error_message
                checkpoint.completed_at = now
                checkpoint.lease_owner = checkpoint.lease_expires_at = None
                mark_blocked(
                    run,
                    now,
                    message=outcome.error_message or "步骤等待确认",
                )
                self._project_blocked_transition(
                    workflow=workflow,
                    context=context,
                    db=db,
                    run=run,
                    now=now,
                    error_code=outcome.error_code or "step_blocked",
                    error_message=outcome.error_message or "步骤等待确认",
                    recovery_action=outcome.recovery_action,
                    step_name=definition.name,
                )
                self.execution_events.append(
                    db, run=run, checkpoint=checkpoint,
                    event_type=ExecutionEventType.STEP_BLOCKED,
                    severity=ExecutionSeverity.WARNING,
                    error_category=(outcome.error_category.value if outcome.error_category else None),
                    error_code=outcome.error_code,
                )
                db.commit()
                log_event(logger, 30, "workflow_step_blocked", workflow_run_id=context.workflow_run_id, step_name=definition.name, error_code=outcome.error_code)
                return StepRunResult(
                    "blocked", {}, step_status=StepStatus.BLOCKED.value,
                    error_category=outcome.error_category,
                    error_code=outcome.error_code,
                    recovery_action=outcome.recovery_action or RecoveryAction.REVIEW_REQUIRED,
                )
            if outcome.kind in {StepOutcomeKind.RETRY_WAIT, StepOutcomeKind.ACTIVITY_RETRY_WAIT, StepOutcomeKind.WAITING_EXTERNAL}:
                return self._defer(db, workflow, definition, context, run, checkpoint, outcome)
            error_code = outcome.error_code or "step_failed"
            error_message = outcome.error_message or "步骤执行失败"
            self.repository.fail(
                checkpoint,
                error_code=error_code,
                error_message=error_message,
                error_category=outcome.error_category.value if outcome.error_category else StepErrorCategory.INTERNAL.value,
            )
            # “开始执行”与 checkpoint.running 一起提交；进程崩溃后页面不会看到
            # 一个不存在对应检查点的开始事件。
            self.execution_events.append(
                db, run=run, checkpoint=checkpoint,
                event_type=ExecutionEventType.STEP_FAILED,
                severity=ExecutionSeverity.ERROR,
                error_category=(outcome.error_category.value if outcome.error_category else StepErrorCategory.INTERNAL.value),
                error_code=error_code,
            )
            metrics.increment("recruit_workflow_step_failure", workflow_type=workflow.workflow_type, step_name=definition.name, retryable=False)
            log_event(logger, 40, "workflow_step_failed", workflow_run_id=context.workflow_run_id, step_name=definition.name, error_code=error_code, error_message=error_message)
            self._finalize_workflow_failure(
                workflow=workflow, context=context, db=db, run=run, checkpoint=checkpoint,
                error=RuntimeError(error_message),
                error_category=outcome.error_category or StepErrorCategory.INTERNAL,
                error_code=error_code,
                recovery_action=outcome.recovery_action,
            )
            db.commit()
            raise StepTerminalFailure(
                definition.name,
                error_code,
                error_message,
                outcome.error_category or StepErrorCategory.INTERNAL,
                outcome.recovery_action or RecoveryAction.USER_RETRY,
            )

    def _defer(self, db: Any, workflow: WorkflowSpec, definition: StepDefinition, context: WorkflowContext, run: Any, checkpoint: WorkflowStepCheckpoint, outcome: StepOutcome) -> StepRunResult:
        """只延迟当前 Step；此前成功的 Step 与其 Artifact 不会重跑。"""
        if checkpoint.deadline_at is not None and checkpoint.deadline_at <= _now():
            self.repository.fail(
                checkpoint,
                error_code="step_deadline_exceeded",
                error_message="步骤超过总执行时限",
                error_category=StepErrorCategory.INFRASTRUCTURE.value,
            )
            # “开始执行”与 checkpoint.running 一起提交；进程崩溃后页面不会看到
            # 一个不存在对应检查点的开始事件。
            self.execution_events.append(
                db, run=run, checkpoint=checkpoint,
                event_type=ExecutionEventType.STEP_FAILED,
                severity=ExecutionSeverity.ERROR,
                error_category=StepErrorCategory.INFRASTRUCTURE.value,
                error_code="step_deadline_exceeded",
            )
            self._finalize_workflow_failure(
                workflow=workflow, context=context, db=db, run=run, checkpoint=checkpoint,
                error=RuntimeError("步骤超过总执行时限"),
                error_category=StepErrorCategory.INFRASTRUCTURE,
                error_code="step_deadline_exceeded",
            )
            db.commit()
            raise StepTerminalFailure(
                definition.name,
                "step_deadline_exceeded",
                "步骤超过总执行时限",
                StepErrorCategory.INFRASTRUCTURE,
            )
        if (
            outcome.kind == StepOutcomeKind.WAITING_EXTERNAL
            and checkpoint.poll_count >= (
                checkpoint.max_poll_attempts_snapshot
                or definition.policy.max_poll_attempts
            )
        ):
            self.repository.fail(
                checkpoint,
                error_code="external_poll_attempts_exhausted",
                error_message="外部任务在允许的轮询次数内未返回结果",
                error_category=StepErrorCategory.EXTERNAL_TRANSIENT.value,
            )
            # “开始执行”与 checkpoint.running 一起提交；进程崩溃后页面不会看到
            # 一个不存在对应检查点的开始事件。
            self.execution_events.append(
                db, run=run, checkpoint=checkpoint,
                event_type=ExecutionEventType.STEP_FAILED,
                severity=ExecutionSeverity.ERROR,
                error_category=StepErrorCategory.EXTERNAL_TRANSIENT.value,
                error_code="external_poll_attempts_exhausted",
            )
            self._finalize_workflow_failure(
                workflow=workflow, context=context, db=db, run=run, checkpoint=checkpoint,
                error=RuntimeError("外部任务在允许的轮询次数内未返回结果"),
                error_category=StepErrorCategory.EXTERNAL_TRANSIENT,
                error_code="external_poll_attempts_exhausted",
            )
            db.commit()
            raise StepTerminalFailure(
                definition.name,
                "external_poll_attempts_exhausted",
                "外部任务在允许的轮询次数内未返回结果",
                StepErrorCategory.EXTERNAL_TRANSIENT,
            )
        max_attempts = checkpoint.max_attempts_snapshot or definition.policy.max_attempts
        if outcome.kind == StepOutcomeKind.RETRY_WAIT and checkpoint.attempt_count >= max_attempts:
            self.repository.fail(
                checkpoint,
                error_code="step_attempts_exhausted",
                error_message="步骤已达到最大重试次数",
                error_category=outcome.error_category.value if outcome.error_category else StepErrorCategory.INFRASTRUCTURE.value,
            )
            # “开始执行”与 checkpoint.running 一起提交；进程崩溃后页面不会看到
            # 一个不存在对应检查点的开始事件。
            self.execution_events.append(
                db, run=run, checkpoint=checkpoint,
                event_type=ExecutionEventType.STEP_FAILED,
                severity=ExecutionSeverity.ERROR,
                error_category=(outcome.error_category.value if outcome.error_category else StepErrorCategory.INFRASTRUCTURE.value),
                error_code="step_attempts_exhausted",
            )
            self._finalize_workflow_failure(
                workflow=workflow, context=context, db=db, run=run, checkpoint=checkpoint,
                error=RuntimeError("步骤已达到最大重试次数"),
                error_category=outcome.error_category or StepErrorCategory.INFRASTRUCTURE,
                error_code="step_attempts_exhausted",
            )
            db.commit()
            raise StepTerminalFailure(
                definition.name,
                "step_attempts_exhausted",
                "步骤已达到最大重试次数",
                outcome.error_category or StepErrorCategory.INFRASTRUCTURE,
            )
        status = (
            StepStatus.RETRY_WAIT if outcome.kind == StepOutcomeKind.RETRY_WAIT
            else StepStatus.ACTIVITY_RETRY_WAIT if outcome.kind == StepOutcomeKind.ACTIVITY_RETRY_WAIT
            else StepStatus.WAITING_EXTERNAL
        )
        available_at = self.repository.defer(
            checkpoint, status=status,
            after_seconds=(
                outcome.retry_after_seconds
                or (
                    definition.policy.poll_interval_seconds
                    if outcome.kind == StepOutcomeKind.WAITING_EXTERNAL
                    else self._backoff(definition, checkpoint.attempt_count)
                )
            ),
            error_category=outcome.error_category.value if outcome.error_category else None,
            error_code=outcome.error_code, error_message=outcome.error_message,
            external_job_id=outcome.external_job_id,
        )
        retry_error = RuntimeError(outcome.error_message or status.value)
        schedule_retry(run, _now(), retry_error, available_at=available_at)
        self._project_transition(
            workflow=workflow, context=context, db=db, run=run, now=_now(),
            error=retry_error, retrying=True,
            recovery_action=outcome.recovery_action,
        )
        # 延迟事件只保存下一次尝试时间和稳定错误码；供应商异常正文不进入前端时间线。
        self.execution_events.append(
            db, run=run, checkpoint=checkpoint,
            event_type=ExecutionEventType.STEP_DEFERRED,
            severity=ExecutionSeverity.WARNING,
            error_category=(outcome.error_category.value if outcome.error_category else None),
            error_code=outcome.error_code,
            diagnostics={
                "status": status.value,
                "next_attempt_at": available_at,
                "max_attempts": checkpoint.max_attempts_snapshot,
                "max_poll_attempts": checkpoint.max_poll_attempts_snapshot,
            },
        )
        metrics.increment("recruit_workflow_step_deferred", workflow_type=workflow.workflow_type, step_name=definition.name, status=status.value)
        log_event(logger, 30, "workflow_step_deferred", workflow_run_id=run.workflow_run_id, step_name=definition.name, status=status.value, attempt=checkpoint.attempt_count, next_attempt_at=available_at)
        db.commit()
        return StepRunResult(
            "deferred", {}, step_status=status.value, next_attempt_at=available_at,
            error_category=outcome.error_category, error_code=outcome.error_code,
            recovery_action=outcome.recovery_action,
        )

    def run_plan(self, workflow: WorkflowSpec, context: WorkflowContext) -> RunPlanResult:
        """执行冻结步骤计划，并向 Worker 返回可投影的明确结果，而不是布尔值。"""
        try:
            for definition in workflow.steps:
                result = self.run(workflow=workflow, definition=definition, context=context)
                if result.status == "deferred":
                    return RunPlanResult(
                        status=RunPlanStatus.DEFERRED,
                        current_step_name=definition.name,
                        current_step_status=result.step_status,
                        error_category=result.error_category,
                        error_code=result.error_code,
                        recovery_action=result.recovery_action,
                        next_attempt_at=result.next_attempt_at,
                    )
                if result.status == "blocked":
                    return RunPlanResult(
                        status=RunPlanStatus.BLOCKED,
                        current_step_name=definition.name,
                        current_step_status=result.step_status,
                        error_category=result.error_category,
                        error_code=result.error_code,
                        recovery_action=result.recovery_action,
                    )
        except StepTerminalFailure as error:
            return RunPlanResult(
                status=RunPlanStatus.FAILED,
                current_step_name=error.step_name,
                current_step_status=StepStatus.FAILED.value,
                error_category=error.error_category,
                error_code=error.error_code,
                error_message=error.error_message,
                recovery_action=error.recovery_action or RecoveryAction.USER_RETRY,
            )
        with SessionLocal() as db:
            run = self.repository.require_owned_run(db, context.workflow_run_id, context.worker_id)
            mark_completed(run, _now())
            self.execution_events.append(
                db, run=run, event_type=ExecutionEventType.WORKFLOW_COMPLETED,
            )
            db.commit()
        return RunPlanResult(status=RunPlanStatus.COMPLETED)

    @staticmethod
    def _retry_outcome(
        _definition: StepDefinition,
        code: str,
        message: str,
        category: StepErrorCategory = StepErrorCategory.INFRASTRUCTURE,
        retry_after_seconds: int | None = None,
    ) -> StepOutcome:
        # 供应商若给出 Retry-After 则优先采用；否则 _defer 按检查点尝试次数指数退避。
        return StepOutcome(
            StepOutcomeKind.RETRY_WAIT,
            error_category=category,
            error_code=code,
            error_message=message,
            retry_after_seconds=retry_after_seconds,
        )

    @staticmethod
    def _external_outcome(definition: StepDefinition, error: ExternalServiceError) -> StepOutcome:
        if error.retryable:
            return StepRunner._retry_outcome(
                definition,
                f"{error.service}:{error.code}",
                error.message,
                StepErrorCategory.EXTERNAL_TRANSIENT,
                retry_after_seconds=(
                    int(error.context["retry_after_seconds"])
                    if isinstance(error.context.get("retry_after_seconds"), int)
                    else None
                ),
            )
        return StepOutcome.failed(
            error_code=f"{error.service}:{error.code}",
            error_message=error.message,
            error_category=StepErrorCategory.EXTERNAL_PERMANENT,
        )

    @staticmethod
    def _backoff(definition: StepDefinition, attempt_count: int) -> int:
        base = min(definition.policy.max_backoff_seconds, definition.policy.initial_backoff_seconds * (2 ** max(0, attempt_count - 1)))
        return max(1, int(base * uniform(1 - definition.policy.jitter_ratio, 1 + definition.policy.jitter_ratio)))


