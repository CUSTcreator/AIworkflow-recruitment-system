"""一次外部调用的统一适配边界。

ExternalActivity 只报告调用事实：成功、等待异步结果、可重试失败或永久失败。它不写
业务数据、不修改领域状态、不实现业务重试循环；这些决策分别属于 StepRunner 和领域
Service。存在工作流上下文时，它只把调用事实暂存到当前 Step；由 StepRunner 与检查点状态在同一事务中写入执行时间线。为兼容现有适配器，``call`` 仍在失败时抛出 ExternalServiceError。
"""
from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from enum import StrEnum
from hashlib import sha256
from time import perf_counter
from typing import Generic, TypeVar

from backend.app.infrastructure.observability.logging import get_logger, log_event
from backend.app.infrastructure.observability.metrics import metrics
from backend.app.infrastructure.observability.event_contracts import ExecutionEventType
from backend.app.infrastructure.observability.activity_contracts import ActivityType
from backend.app.infrastructure.workflow_runtime.external_error_policy import (
    classify_external_error,
)
from backend.app.shared.errors import ExternalServiceError
from backend.app.shared.workflows import StepErrorCategory
from recruitment_ai_core.llm.errors import LLMResponseError


T = TypeVar("T")
logger = get_logger(__name__)


# StepRunner 在执行 handler 时绑定它；适配器可无侵入读取，避免每个业务 Workflow
# 都手工透传重试上下文。ContextVar 只在当前线程/调用链生效，不会泄漏到其他任务。
_active_context: ContextVar["ExternalActivityContext | None"] = ContextVar(
    "recruit_external_activity_context", default=None
)
# 每次 Step 执行都创建独立事件缓冲区。外部适配器只能报告事实，不能自行提交
# ``workflow_execution_events``，从而避免轨迹先于检查点或业务事务提交。
_active_events: ContextVar["list[ExternalActivityEvent] | None"] = ContextVar(
    "recruit_external_activity_events", default=None
)


class ExternalActivityStatus(StrEnum):
    """外部层报告的事实状态；StepRunner 将其映射为 StepOutcome。"""

    SUCCEEDED = "succeeded"
    WAITING_EXTERNAL = "waiting_external"
    RETRYABLE_FAILURE = "retryable_failure"
    PERMANENT_FAILURE = "permanent_failure"


@dataclass(frozen=True, slots=True)
class ExternalActivityEvent:
    """外部适配器上报给当前 Step 的安全调用事实，不包含原始回包。"""

    event_type: ExecutionEventType
    service: str
    operation: str
    error_category: str | None = None
    error_code: str | None = None
    activity_type: ActivityType | str = ActivityType.HTTP
    activity_key: str | None = None
    attempt: int | None = None
    duration_ms: float | None = None
    retryable: bool | None = None
    degraded: bool | None = None
    external_request_id: str | None = None
    diagnostics: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExternalActivityContext:
    """一次外部操作所需的稳定执行信息，不携带 ORM 或业务对象。"""

    workflow_run_id: str
    step_name: str
    attempt_number: int
    idempotency_key: str
    external_request_id: str
    timeout_seconds: int
    operation: str

    def child_request_id(self, fingerprint: str = "") -> str:
        """同一 Step 内多个外部子请求可从稳定根请求 ID 派生，不重新生成随机 ID。"""
        value = f"{self.external_request_id}:{self.operation}:{fingerprint}"
        return sha256(value.encode("utf-8")).hexdigest()

    def for_operation(self, operation: str, *, fingerprint: str = "") -> "ExternalActivityContext":
        """为同一 Step 内的外部子操作派生稳定、彼此隔离的幂等键。"""
        return replace(
            self,
            operation=operation,
            idempotency_key=self.child_request_id(f"{operation}:{fingerprint}"),
        )


@contextmanager
def external_activity_scope(context: ExternalActivityContext):
    """绑定当前 Step 的外部调用上下文和事件缓冲区。"""
    context_token = _active_context.set(context)
    # 嵌套活动沿用父 Step 的事件缓冲区，最终仍由 StepRunner 与检查点同事务发布。
    # 最外层才创建缓冲区，避免 ActivityRunner 的子范围吞掉调用事实。
    events = _active_events.get()
    if events is None:
        events = []
    events_token = _active_events.set(events)
    try:
        yield events
    finally:
        _active_events.reset(events_token)
        _active_context.reset(context_token)


def _infer_activity_type(service: str) -> ActivityType:
    """按服务名给未显式声明的调用归类，业务适配器可在后续覆盖。"""
    value = service.lower()
    if "llm" in value or "openai" in value or "silicon" in value:
        return ActivityType.LLM
    if "mineru" in value:
        return ActivityType.MINERU
    if "minio" in value or "object" in value or "storage" in value:
        return ActivityType.MINIO
    return ActivityType.HTTP


def report_external_activity_event(
    *,
    event_type: ExecutionEventType,
    service: str,
    operation: str,
    error_category: str | None = None,
    error_code: str | None = None,
    activity_type: ActivityType | str = ActivityType.HTTP,
    activity_key: str | None = None,
    attempt: int | None = None,
    duration_ms: float | None = None,
    retryable: bool | None = None,
    degraded: bool | None = None,
    external_request_id: str | None = None,
    diagnostics: dict[str, object] | None = None,
) -> None:
    """向当前 Step 报告统一 Activity 事件；原始回包和业务文本不得进入时间线。"""
    context = _active_context.get()
    if activity_type == ActivityType.HTTP:
        activity_type = _infer_activity_type(service)
    attempt = attempt if attempt is not None else (context.attempt_number if context else None)
    external_request_id = external_request_id or (context.external_request_id if context else None)
    events = _active_events.get()
    if events is not None:
        events.append(ExternalActivityEvent(
            event_type=event_type,
            service=service,
            operation=operation,
            error_category=error_category,
            error_code=error_code,
            activity_type=activity_type,
            activity_key=activity_key,
            attempt=attempt,
            duration_ms=duration_ms,
            retryable=retryable,
            degraded=degraded,
            external_request_id=external_request_id,
            diagnostics=diagnostics or {},
        ))

def current_external_activity_context(
    *, operation: str, fingerprint: str = ""
) -> ExternalActivityContext | None:
    """返回当前 Step 派生的外部上下文；普通 Web 请求中则返回 ``None``。"""
    context = _active_context.get()
    if context is None:
        return None
    return context.for_operation(operation, fingerprint=fingerprint)


@dataclass(frozen=True, slots=True)
class ExternalActivityResult(Generic[T]):
    """外部适配器向 StepRunner 上报的标准结果；大回包应由调用方转为 Artifact。"""

    status: ExternalActivityStatus
    value: T | None = None
    external_job_id: str | None = None
    retry_after_seconds: int | None = None
    error_category: StepErrorCategory | None = None
    error_code: str | None = None
    diagnostic_message: str = ""


@dataclass(frozen=True, slots=True)
class ExternalCallContext:
    """兼容旧调用点的稳定外部请求 ID 生成器。"""

    root_request_id: str
    operation: str
    request_id: str

    @classmethod
    def from_root(cls, root_request_id: str, operation: str, *, fingerprint: str = "") -> "ExternalCallContext":
        value = f"{root_request_id}:{operation}:{fingerprint}"
        return cls(root_request_id=root_request_id, operation=operation, request_id=sha256(value.encode("utf-8")).hexdigest())


class ExternalActivity:
    """一次外部调用的错误分类、幂等键传递、响应校验和可观测性入口。"""

    def execute(
        self,
        *,
        service: str,
        operation: str,
        idempotency_key: str,
        invoke: Callable[[str], T],
        validate: Callable[[T], T] | None = None,
        context: ExternalActivityContext | None = None,
        activity_type: ActivityType | str | None = None,
        activity_key: str | None = None,
        diagnostics: dict[str, object] | None = None,
    ) -> ExternalActivityResult[T]:
        """执行一次请求并返回标准结果，不进行隐藏重试。

        ``invoke`` 必须把当前 Step 的剩余时间预算传给底层 HTTP/SDK 客户端；同步
        Python 线程无法安全强杀一个已经阻塞的第三方 SDK 调用，因此本层负责统一
        分类和上报，而不是伪造无法兑现的超时中断。
        """
        context = context or current_external_activity_context(
            operation=operation, fingerprint=idempotency_key
        )
        labels = {"service": service, "operation": operation}
        activity_type = activity_type or _infer_activity_type(service)
        diagnostics = diagnostics or {}
        request_key = context.idempotency_key if context is not None else idempotency_key
        # 外部层只写技术事实到 stdout，不写 WorkflowExecutionEvent：它不知道本次
        # 调用是否代表业务步骤成功，最终状态必须由 StepRunner 在事务中统一发布。
        log_event(
            logger, 20, "external_activity_started", **labels,
            workflow_run_id=(context.workflow_run_id if context else ""),
            step_name=(context.step_name if context else ""),
            external_request_id=(context.external_request_id if context else ""),
        )
        report_external_activity_event( event_type=ExecutionEventType.EXTERNAL_ACTIVITY_STARTED,
            service=service, operation=operation, activity_type=activity_type,
            activity_key=activity_key, attempt=(context.attempt_number if context else None),
            external_request_id=(context.external_request_id if context else None), diagnostics=diagnostics,
        )
        started = perf_counter()
        try:
            with metrics.measure("recruit_external_activity", **labels):
                response = invoke(request_key)
            if validate is not None:
                response = validate(response)
        except ValueError as error:
            # 响应校验失败仍只作为技术事件/标准结果上报；不把供应商正文写进时间线。
            # 响应校验失败属于不可重试的输入/协议问题，不能伪装成供应商临时故障。
            metrics.increment("recruit_external_activity_failure", **labels, retryable=False)
            report_external_activity_event( event_type=ExecutionEventType.EXTERNAL_ACTIVITY_FAILED,
                service=service, operation=operation,
                error_category=StepErrorCategory.VALIDATION.value,
                error_code=f"{operation}_response_invalid",
            )
            return ExternalActivityResult(
                status=ExternalActivityStatus.PERMANENT_FAILURE,
                error_category=StepErrorCategory.VALIDATION,
                error_code=f"{operation}_response_invalid",
                diagnostic_message=str(error),
            )
        except LLMResponseError as error:
            # LLM 已返回，但 JSON/Schema/协议不满足合同；这不是网络瞬态故障。
            # 格式修复由业务 handler 显式做一次，之后直接进入 Activity 降级/阻塞策略。
            metrics.increment("recruit_external_activity_failure", **labels, retryable=False)
            report_external_activity_event(
                event_type=ExecutionEventType.EXTERNAL_ACTIVITY_FAILED,
                service=service, operation=operation,
                error_category=StepErrorCategory.VALIDATION.value,
                error_code=f"{operation}_response_invalid",
            )
            return ExternalActivityResult(
                status=ExternalActivityStatus.PERMANENT_FAILURE,
                error_category=StepErrorCategory.VALIDATION,
                error_code=f"{operation}_response_invalid",
                diagnostic_message=str(error),
            )
        except Exception as error:
            mapped = classify_external_error(service, operation, error)
            category = StepErrorCategory.EXTERNAL_TRANSIENT if mapped.retryable else StepErrorCategory.EXTERNAL_PERMANENT
            status = ExternalActivityStatus.RETRYABLE_FAILURE if mapped.retryable else ExternalActivityStatus.PERMANENT_FAILURE
            metrics.increment("recruit_external_activity_failure", **labels, retryable=mapped.retryable)
            # 失败日志记录服务、操作和稳定错误码；原始异常仅保留在受脱敏保护的日志字段。
            log_event(
                logger, 30 if mapped.retryable else 40, "external_activity_failed", **labels,
                retryable=mapped.retryable, code=mapped.code,
                external_request_id=(context.external_request_id if context else ""),
            )
            report_external_activity_event( event_type=ExecutionEventType.EXTERNAL_ACTIVITY_FAILED,
                service=service, operation=operation,
                error_category=category.value, error_code=mapped.code,
            )
            return ExternalActivityResult(
                status=status, error_category=category, error_code=mapped.code,
                retry_after_seconds=(
                    int(mapped.context["retry_after_seconds"])
                    if isinstance(mapped.context.get("retry_after_seconds"), int)
                    else None
                ),
                diagnostic_message=mapped.message,
            )
        duration_ms = round((perf_counter() - started) * 1000, 2)
        metrics.increment("recruit_external_activity_success", **labels)
        log_event(
            logger, 20, "external_activity_succeeded", **labels,
            duration_ms=duration_ms,
            workflow_run_id=(context.workflow_run_id if context else ""),
            step_name=(context.step_name if context else ""),
            external_request_id=(context.external_request_id if context else ""),
        )
        report_external_activity_event( event_type=ExecutionEventType.EXTERNAL_ACTIVITY_SUCCEEDED,
            service=service, operation=operation,
        )
        return ExternalActivityResult(status=ExternalActivityStatus.SUCCEEDED, value=response)

    def call(
        self,
        *,
        service: str,
        operation: str,
        idempotency_key: str,
        invoke: Callable[[str], T],
        validate: Callable[[T], T] | None = None,
        context: ExternalActivityContext | None = None,
        activity_type: ActivityType | str | None = None,
        activity_key: str | None = None,
        diagnostics: dict[str, object] | None = None,
    ) -> T:
        """兼容同步适配器：成功返回值，失败抛出已规范化的 ExternalServiceError。"""
        result = self.execute(
            service=service, operation=operation, idempotency_key=idempotency_key,
            invoke=invoke, validate=validate, context=context,
            activity_type=activity_type, activity_key=activity_key, diagnostics=diagnostics,
        )
        if result.status == ExternalActivityStatus.SUCCEEDED:
            return result.value  # type: ignore[return-value]
        raise ExternalServiceError(
            service, result.error_code or f"{operation}_failed", result.diagnostic_message,
            retryable=result.status == ExternalActivityStatus.RETRYABLE_FAILURE,
            context={
                "error_category": result.error_category.value if result.error_category else "",
                **(
                    {"retry_after_seconds": result.retry_after_seconds}
                    if result.retry_after_seconds is not None
                    else {}
                ),
            },
        )


