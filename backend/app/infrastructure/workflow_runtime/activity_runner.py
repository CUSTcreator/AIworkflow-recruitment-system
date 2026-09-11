"""Step 内 Activity 的恢复、降级与阻塞执行器。

``StepRunner`` 负责业务 Step 的生命周期；本模块只负责 Step 内一次外部调用或一组
可独立恢复调用。执行检查点与业务可用性结论严格分离：重试、幂等、租约属于运行时；
是否可用标准能力模型降级、是否必须请用户处理，必须由领域服务显式声明。
"""
from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from backend.app.db.session import SessionLocal
from backend.app.infrastructure.workflow_runtime.artifact_store import WorkflowArtifactStore
from backend.app.infrastructure.workflow_runtime.external_activity import (
    current_external_activity_context,
    external_activity_scope,
)
from backend.app.models.entities import WorkflowActivityCheckpoint, WorkflowRun
from backend.app.shared.errors import BusinessError, ExternalServiceError, retryable_decision
from backend.app.shared.workflows import (
    ActivityExecutionStatus,
    ActivityExhaustionPolicy,
    ActivityOutcomeKind,
    StepErrorCategory,
    WorkflowRunStatus,
)
from backend.app.shared.workflows.status_contracts import ensure_activity_transition
from recruitment_ai_core.llm.errors import LLMCallError, LLMResponseError
from recruitment_ai_core.llm.schema_validator import JSONSchemaValidationError


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _stable_hash(value: dict[str, Any]) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class ActivityPolicy:
    """单个外部活动的重试策略；不消耗父 Step 的重试次数。"""

    max_attempts: int = 3
    retry_after_seconds: int = 10
    backoff_multiplier: float = 2.0
    max_retry_after_seconds: int = 300


@dataclass(frozen=True, slots=True)
class ActivityContext:
    """传给活动处理器的稳定运行时信息，不包含 ORM 对象。"""

    workflow_run_id: str
    parent_step_name: str
    activity_key: str
    input_hash: str
    attempt_number: int
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class ActivityResolution:
    """领域服务在 Activity 重试耗尽后给出的显式处理结论。

    ``payload`` 必须仍遵循该 Activity 的正常输出合同。这样父 Step 不会因降级而读取
    另一套临时模型；例如 WorkUnit 失败时，服务只能返回标准 WorkUnit 的保守实例。
    """

    outcome_kind: ActivityOutcomeKind
    payload: dict[str, Any]
    resolution_code: str
    quality_summary: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.outcome_kind not in {ActivityOutcomeKind.DEGRADED, ActivityOutcomeKind.BLOCKED}:
            raise ValueError("workflow_activity_resolution_kind_invalid")
        if not self.resolution_code.strip() or not isinstance(self.payload, dict):
            raise ValueError("workflow_activity_resolution_invalid")


ActivityHandler = Callable[[ActivityContext], dict[str, Any]]
ActivityExhaustedHandler = Callable[[ActivityContext, Exception], ActivityResolution]
ActivityDegradationPredicate = Callable[[Exception], bool]


def is_safe_model_degradation_error(error: Exception) -> bool:
    """Whether a local fallback may safely replace a known model-boundary error.

    A fallback is a business decision, not a generic exception handler. It is only
    valid when the owning service can produce the same output contract without the
    model. Programming defects and business-rule failures must remain failures.
    """

    return isinstance(
        error,
        (
            ExternalServiceError,
            LLMCallError,
            LLMResponseError,
            JSONSchemaValidationError,
            TimeoutError,
            ConnectionError,
            OSError,
        ),
    )


@dataclass(frozen=True, slots=True)
class ActivityDefinition:
    """Step 内一个可独立恢复的活动声明。

    正常路径不因为这里的字段增加任何 LLM 请求。只有外部重试耗尽时，运行时才按
    ``exhaustion_policy`` 调用领域服务提供的纯本地回退处理器或投影为业务阻塞。
    """

    activity_key: str
    input_data: dict[str, Any]
    handler: ActivityHandler
    policy: ActivityPolicy = field(default_factory=ActivityPolicy)
    exhaustion_policy: ActivityExhaustionPolicy = ActivityExhaustionPolicy.FAIL_AS_SYSTEM_ERROR
    on_exhausted: ActivityExhaustedHandler | None = None
    can_degrade: ActivityDegradationPredicate | None = None

    def __post_init__(self) -> None:
        if self.exhaustion_policy == ActivityExhaustionPolicy.FAIL_AS_SYSTEM_ERROR:
            if self.on_exhausted is not None or self.can_degrade is not None:
                raise ValueError("workflow_activity_unexpected_exhausted_handler")
        elif self.on_exhausted is None or self.can_degrade is None:
            raise ValueError("workflow_activity_exhaustion_contract_required")


@dataclass(frozen=True, slots=True)
class ActivityBatchResult:
    """活动组结果，供父 Step 显式投影为继续、等待、阻塞或失败。

    ``status`` 是批次结论；``outcome_kinds`` 保存每项的 completed/degraded 质量。
    ``retry_wait`` 与 ``failed`` 仍是运行时结果，不能写入业务读模型。
    """

    status: str
    results: dict[str, dict[str, Any]] = field(default_factory=dict)
    outcome_kinds: dict[str, str] = field(default_factory=dict)
    quality_summaries: dict[str, dict[str, Any]] = field(default_factory=dict)
    retry_after_seconds: int | None = None
    error_category: StepErrorCategory | None = None
    error_code: str | None = None
    error_message: str | None = None

    @property
    def is_usable(self) -> bool:
        return self.status in {ActivityOutcomeKind.COMPLETED.value, ActivityOutcomeKind.DEGRADED.value}


class ActivityRunner:
    """活动级检查点、幂等复用、退避和领域降级的唯一入口。"""

    def run_many(
        self,
        *,
        workflow_run_id: str,
        worker_id: str,
        parent_step_name: str,
        activities: list[ActivityDefinition],
        parallel: bool = False,
    ) -> ActivityBatchResult:
        """执行或复用一组可独立恢复的活动。

        默认顺序执行；需要 token 分批扇出时可显式启用 ``parallel``。已降级的
        Activity 会保留标准合同的结果并继续后续活动；只有等待重试、业务阻塞或
        系统失败才会中断当前 Step。
        """
        results: dict[str, dict[str, Any]] = {}
        outcome_kinds: dict[str, str] = {}
        quality_summaries: dict[str, dict[str, Any]] = {}
        has_degraded = False
        ordered_results: list[ActivityBatchResult]
        if parallel and len(activities) > 1:
            with ThreadPoolExecutor(max_workers=min(8, len(activities))) as executor:
                futures = {
                    executor.submit(
                        self._run_one,
                        workflow_run_id=workflow_run_id,
                        worker_id=worker_id,
                        parent_step_name=parent_step_name,
                        definition=definition,
                    ): index
                    for index, definition in enumerate(activities)
                }
                completed: dict[int, ActivityBatchResult] = {
                    futures[future]: future.result() for future in as_completed(futures)
                }
            ordered_results = [completed[index] for index in range(len(activities))]
        else:
            ordered_results = [
                self._run_one(
                    workflow_run_id=workflow_run_id,
                    worker_id=worker_id,
                    parent_step_name=parent_step_name,
                    definition=definition,
                )
                for definition in activities
            ]

        terminal: ActivityBatchResult | None = None
        for result in ordered_results:
            results.update(result.results)
            outcome_kinds.update(result.outcome_kinds)
            quality_summaries.update(result.quality_summaries)
            if result.status == ActivityOutcomeKind.DEGRADED.value:
                has_degraded = True
                continue
            if result.status != ActivityOutcomeKind.COMPLETED.value and terminal is None:
                terminal = result
        if terminal is not None:
            return ActivityBatchResult(
                status=terminal.status,
                results=results,
                outcome_kinds=outcome_kinds,
                quality_summaries=quality_summaries,
                retry_after_seconds=terminal.retry_after_seconds,
                error_category=terminal.error_category,
                error_code=terminal.error_code,
                error_message=terminal.error_message,
            )
        return ActivityBatchResult(
            status=(ActivityOutcomeKind.DEGRADED.value if has_degraded else ActivityOutcomeKind.COMPLETED.value),
            results=results,
            outcome_kinds=outcome_kinds,
            quality_summaries=quality_summaries,
        )

    def _run_one(
        self,
        *,
        workflow_run_id: str,
        worker_id: str,
        parent_step_name: str,
        definition: ActivityDefinition,
    ) -> ActivityBatchResult:
        input_hash = _stable_hash(definition.input_data)
        with SessionLocal() as db:
            self._require_owned_run(db, workflow_run_id, worker_id)
            checkpoint = db.scalar(
                select(WorkflowActivityCheckpoint)
                .where(
                    WorkflowActivityCheckpoint.workflow_run_id == workflow_run_id,
                    WorkflowActivityCheckpoint.parent_step_name == parent_step_name,
                    WorkflowActivityCheckpoint.activity_key == definition.activity_key,
                )
                .with_for_update()
            )
            if checkpoint is None:
                checkpoint = WorkflowActivityCheckpoint(
                    activity_checkpoint_id=f"WAC_{uuid4().hex}",
                    workflow_run_id=workflow_run_id,
                    parent_step_name=parent_step_name,
                    activity_key=definition.activity_key,
                    input_hash=input_hash,
                    status=ActivityExecutionStatus.PENDING.value,
                    max_attempts_snapshot=definition.policy.max_attempts,
                    output_refs_json={},
                    quality_summary_json={},
                )
                db.add(checkpoint)
                db.flush()
            if checkpoint.input_hash != input_hash:
                raise RuntimeError(f"workflow_activity_input_changed:{definition.activity_key}")
            if checkpoint.status == ActivityExecutionStatus.SUCCEEDED.value:
                refs = dict(checkpoint.output_refs_json or {})
                outcome_kind = str(checkpoint.outcome_kind or ActivityOutcomeKind.COMPLETED.value)
                quality_summary = dict(checkpoint.quality_summary_json or {})
                db.commit()
                with SessionLocal() as reader:
                    payload = WorkflowArtifactStore().get_json(reader, str(refs.get("artifactId") or ""))
                return ActivityBatchResult(
                    status=outcome_kind,
                    results={definition.activity_key: payload},
                    outcome_kinds={definition.activity_key: outcome_kind},
                    quality_summaries={definition.activity_key: quality_summary},
                    error_code=checkpoint.resolution_code,
                )
            if checkpoint.status == ActivityExecutionStatus.FAILED.value:
                db.commit()
                return ActivityBatchResult(
                    status="failed", error_code=checkpoint.last_error_code or "activity_failed",
                    error_category=_stored_error_category(checkpoint.last_error_category),
                    error_message=checkpoint.last_error_message or "活动已失败",
                )
            if checkpoint.next_attempt_at is not None and checkpoint.next_attempt_at > _now():
                delay = max(1, int((checkpoint.next_attempt_at - _now()).total_seconds()))
                db.commit()
                return ActivityBatchResult(
                    status="retry_wait", retry_after_seconds=delay,
                    error_category=StepErrorCategory.EXTERNAL_TRANSIENT,
                    error_code=checkpoint.last_error_code or "activity_retry_wait",
                    error_message=checkpoint.last_error_message or "活动等待重试",
                )
            ensure_activity_transition(checkpoint.status, ActivityExecutionStatus.RUNNING)
            checkpoint.status = ActivityExecutionStatus.RUNNING.value
            checkpoint.attempt_count += 1
            checkpoint.started_at = _now()
            checkpoint.updated_at = _now()
            attempt = checkpoint.attempt_count
            idempotency_key = sha256(
                f"{workflow_run_id}:{parent_step_name}:{definition.activity_key}:{input_hash}".encode("utf-8")
            ).hexdigest()
            db.commit()

        context = ActivityContext(
            workflow_run_id=workflow_run_id,
            parent_step_name=parent_step_name,
            activity_key=definition.activity_key,
            input_hash=input_hash,
            attempt_number=attempt,
            idempotency_key=idempotency_key,
        )
        try:
            external_context = current_external_activity_context(
                operation=f"activity:{parent_step_name}:{definition.activity_key}",
                fingerprint=input_hash,
            )
            if external_context is None:
                payload = definition.handler(context)
            else:
                with external_activity_scope(external_context):
                    payload = definition.handler(context)
            if not isinstance(payload, dict):
                raise ValueError("workflow_activity_payload_must_be_object")

            # Activity 的业务产物可以在“执行成功但质量降级”时继续使用。
            # 统一读取标准顶层 degraded 标记，避免把本地回退结果误记为 completed，
            # 从而让父 Step 和业务读模型丢失真实质量状态。
            payload_degraded = bool(payload.get("degraded"))
            return self._persist_resolution(
                workflow_run_id=workflow_run_id,
                worker_id=worker_id,
                parent_step_name=parent_step_name,
                definition=definition,
                payload=payload,
                outcome_kind=(
                    ActivityOutcomeKind.DEGRADED
                    if payload_degraded
                    else ActivityOutcomeKind.COMPLETED
                ),
                resolution_code=(
                    str(payload.get("resolution_code") or "activity_payload_degraded")
                    if payload_degraded
                    else "completed"
                ),
                quality_summary=(
                    {"usable": True, "degraded": True}
                    if payload_degraded
                    else {}
                ),
            )
        except Exception as error:
            return self._record_failure(
                workflow_run_id=workflow_run_id,
                worker_id=worker_id,
                parent_step_name=parent_step_name,
                definition=definition,
                context=context,
                error=error,
            )

    def _record_failure(
        self,
        *,
        workflow_run_id: str,
        worker_id: str,
        parent_step_name: str,
        definition: ActivityDefinition,
        context: ActivityContext,
        error: Exception,
    ) -> ActivityBatchResult:
        retryable = self._is_retryable(error)
        category = StepErrorCategory.EXTERNAL_TRANSIENT if retryable else self._terminal_error_category(error)
        code = self._error_code(error)
        message = str(error)[:2000] or type(error).__name__
        with SessionLocal() as db:
            self._require_owned_run(db, workflow_run_id, worker_id)
            checkpoint = self._require_checkpoint(db, workflow_run_id, parent_step_name, definition.activity_key)
            max_attempts = checkpoint.max_attempts_snapshot or definition.policy.max_attempts
            if retryable and checkpoint.attempt_count < max_attempts:
                ensure_activity_transition(checkpoint.status, ActivityExecutionStatus.RETRY_WAIT)
                checkpoint.status = ActivityExecutionStatus.RETRY_WAIT.value
                retry_after = self._retry_delay_seconds(error, definition.policy, checkpoint.attempt_count)
                checkpoint.next_attempt_at = _now() + timedelta(seconds=retry_after)
                checkpoint.outcome_kind = None
                checkpoint.resolution_code = None
                checkpoint.quality_summary_json = {}
                checkpoint.last_error_category = category.value
                checkpoint.last_error_code = code
                checkpoint.last_error_message = message
                checkpoint.updated_at = _now()
                db.commit()
                return ActivityBatchResult(
                    status="retry_wait", retry_after_seconds=retry_after,
                    error_category=category, error_code=code, error_message=message,
                )

        # 重试耗尽后，运行时只能执行领域服务显式提供的纯本地策略；它不自行臆造
        # 能力数据，也不新增任何 LLM 调用。
        if (
            definition.exhaustion_policy != ActivityExhaustionPolicy.FAIL_AS_SYSTEM_ERROR
            and definition.can_degrade is not None
            and definition.can_degrade(error)
        ):
            try:
                assert definition.on_exhausted is not None
                resolution = definition.on_exhausted(context, error)
                expected = (
                    ActivityOutcomeKind.DEGRADED
                    if definition.exhaustion_policy == ActivityExhaustionPolicy.FALLBACK_TO_STANDARD_MODEL
                    else ActivityOutcomeKind.BLOCKED
                )
                if resolution.outcome_kind != expected:
                    raise ValueError("workflow_activity_exhaustion_policy_mismatch")
                return self._persist_resolution(
                    workflow_run_id=workflow_run_id,
                    worker_id=worker_id,
                    parent_step_name=parent_step_name,
                    definition=definition,
                    payload=resolution.payload,
                    outcome_kind=resolution.outcome_kind,
                    resolution_code=resolution.resolution_code,
                    quality_summary=resolution.quality_summary,
                )
            except Exception as resolution_error:
                category = StepErrorCategory.INTERNAL
                code = f"resolution:{type(resolution_error).__name__}"
                message = str(resolution_error)[:2000] or type(resolution_error).__name__

        with SessionLocal() as db:
            self._require_owned_run(db, workflow_run_id, worker_id)
            checkpoint = self._require_checkpoint(db, workflow_run_id, parent_step_name, definition.activity_key)
            ensure_activity_transition(checkpoint.status, ActivityExecutionStatus.FAILED)
            checkpoint.status = ActivityExecutionStatus.FAILED.value
            checkpoint.next_attempt_at = None
            checkpoint.outcome_kind = None
            checkpoint.resolution_code = None
            checkpoint.quality_summary_json = {}
            checkpoint.last_error_category = category.value
            checkpoint.last_error_code = code
            checkpoint.last_error_message = message
            checkpoint.completed_at = _now()
            checkpoint.updated_at = _now()
            db.commit()
        return ActivityBatchResult(status="failed", error_category=category, error_code=code, error_message=message)

    def _persist_resolution(
        self,
        *,
        workflow_run_id: str,
        worker_id: str,
        parent_step_name: str,
        definition: ActivityDefinition,
        payload: dict[str, Any],
        outcome_kind: ActivityOutcomeKind,
        resolution_code: str,
        quality_summary: dict[str, Any],
    ) -> ActivityBatchResult:
        staged = WorkflowArtifactStore().stage_json(
            workflow_run_id=workflow_run_id,
            step_name=parent_step_name,
            artifact_type=self._artifact_type(parent_step_name, definition.activity_key),
            payload=payload,
        )
        with SessionLocal() as db:
            self._require_owned_run(db, workflow_run_id, worker_id)
            checkpoint = self._require_checkpoint(db, workflow_run_id, parent_step_name, definition.activity_key)
            refs = WorkflowArtifactStore().persist_staged_json(
                db,
                workflow_run_id=workflow_run_id,
                artifact_type=staged.artifact_type,
                staged=staged,
            )
            ensure_activity_transition(checkpoint.status, ActivityExecutionStatus.SUCCEEDED)
            checkpoint.status = ActivityExecutionStatus.SUCCEEDED.value
            checkpoint.output_refs_json = refs
            checkpoint.outcome_kind = outcome_kind.value
            checkpoint.resolution_code = resolution_code
            checkpoint.quality_summary_json = dict(quality_summary)
            checkpoint.next_attempt_at = None
            checkpoint.last_error_category = None
            checkpoint.last_error_code = None
            checkpoint.last_error_message = None
            checkpoint.completed_at = _now()
            checkpoint.updated_at = _now()
            db.commit()
        return ActivityBatchResult(
            status=outcome_kind.value,
            results={definition.activity_key: payload},
            outcome_kinds={definition.activity_key: outcome_kind.value},
            quality_summaries={definition.activity_key: dict(quality_summary)},
            error_code=resolution_code,
        )

    @staticmethod
    def _require_owned_run(db: Any, workflow_run_id: str, worker_id: str) -> WorkflowRun:
        run = db.scalar(select(WorkflowRun).where(WorkflowRun.workflow_run_id == workflow_run_id).with_for_update())
        if run is None or run.status != WorkflowRunStatus.RUNNING.value or run.lease_owner != worker_id:
            raise RuntimeError("workflow_lease_lost")
        return run

    @staticmethod
    def _require_checkpoint(db: Any, workflow_run_id: str, parent_step_name: str, activity_key: str) -> WorkflowActivityCheckpoint:
        checkpoint = db.scalar(
            select(WorkflowActivityCheckpoint)
            .where(
                WorkflowActivityCheckpoint.workflow_run_id == workflow_run_id,
                WorkflowActivityCheckpoint.parent_step_name == parent_step_name,
                WorkflowActivityCheckpoint.activity_key == activity_key,
            )
            .with_for_update()
        )
        if checkpoint is None:
            raise RuntimeError("workflow_activity_checkpoint_missing")
        return checkpoint

    @staticmethod
    def _retry_delay_seconds(error: Exception, policy: ActivityPolicy, attempt_number: int) -> int:
        """计算活动级指数退避；供应商 Retry-After 优先于本地退避。"""
        context = getattr(error, "context", {}) or {}
        hinted = context.get("retry_after_seconds")
        if isinstance(hinted, int) and hinted > 0:
            return max(1, min(hinted, policy.max_retry_after_seconds))
        base = max(1, policy.retry_after_seconds)
        delay = int(base * (policy.backoff_multiplier ** max(0, attempt_number - 1)))
        return max(1, min(delay, policy.max_retry_after_seconds))

    @staticmethod
    def _is_retryable(error: Exception) -> bool:
        decision = retryable_decision(error)
        if decision is not None:
            return decision
        # JSON/Schema/字段错误是响应合同问题，不是外部瞬态故障；格式修复只能由业务 handler 显式做一次。
        return isinstance(error, (TimeoutError, ConnectionError, OSError))

    @staticmethod
    def _error_code(error: Exception) -> str:
        if isinstance(error, ExternalServiceError):
            return f"{error.service}:{error.code}"
        if isinstance(error, BusinessError):
            return error.code
        return type(error).__name__

    @staticmethod
    def _terminal_error_category(error: Exception) -> StepErrorCategory:
        if isinstance(error, BusinessError):
            return StepErrorCategory.BUSINESS_RULE
        if isinstance(error, ValueError):
            return StepErrorCategory.VALIDATION
        return StepErrorCategory.EXTERNAL_PERMANENT

    @staticmethod
    def _artifact_type(parent_step_name: str, activity_key: str) -> str:
        digest = sha256(activity_key.encode("utf-8")).hexdigest()[:16]
        return f"activity_{parent_step_name[:40]}_{digest}"


def _stored_error_category(value: str | None) -> StepErrorCategory:
    """兼容旧检查点；未知历史值按永久外部失败处理。"""
    try:
        return StepErrorCategory(value or StepErrorCategory.EXTERNAL_PERMANENT.value)
    except ValueError:
        return StepErrorCategory.EXTERNAL_PERMANENT
