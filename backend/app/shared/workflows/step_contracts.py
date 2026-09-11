"""步骤级恢复的统一契约。

本模块只定义跨层数据合同：业务 Workflow 声明 StepDefinition，StepRunner 根据
StepOutcome 管理检查点；外部服务、数据库和前端都不能绕过这些状态直接决定重试。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from .status_contracts import StepStatus


class StepOutcomeKind(StrEnum):
    """业务 handler 对 StepRunner 的标准化结论。"""

    SUCCEEDED = "succeeded"
    RETRY_WAIT = "retry_wait"
    # 由 ActivityRunner 产生；StepRunner 只负责延迟重新进入同一 Step。
    ACTIVITY_RETRY_WAIT = "activity_retry_wait"
    WAITING_EXTERNAL = "waiting_external"
    FAILED = "failed"
    BLOCKED = "blocked"


class StepErrorCategory(StrEnum):
    """持久化错误分类；页面通过读模型映射文案，不能解析原始异常文本。"""

    EXTERNAL_TRANSIENT = "external_transient"
    EXTERNAL_PERMANENT = "external_permanent"
    VALIDATION = "validation"
    BUSINESS_RULE = "business_rule"
    INFRASTRUCTURE = "infrastructure"
    INTERNAL = "internal"


class RecoveryAction(StrEnum):
    """业务页面可执行的恢复动作。

    ``StepErrorCategory`` 说明错误来源，``RecoveryAction`` 说明系统或用户
    下一步该做什么；两者必须分开，避免将业务待确认误判为技术失败。
    """

    AUTO_RETRY = "auto_retry"
    USER_RETRY = "user_retry"
    REVIEW_REQUIRED = "review_required"
    CONTINUE_MANUALLY = "continue_manually"

class RunPlanStatus(StrEnum):
    """StepRunner 向 Worker 返回的整条 Workflow 本次运行结论。"""

    COMPLETED = "completed"
    DEFERRED = "deferred"
    BLOCKED = "blocked"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class RunPlanResult:
    """Worker 只消费该结果，不再自行推断或重复执行 Step 级重试。"""

    status: RunPlanStatus
    current_step_name: str = ""
    current_step_status: str = ""
    error_category: StepErrorCategory | None = None
    error_code: str | None = None
    error_message: str | None = None
    recovery_action: RecoveryAction | None = None
    next_attempt_at: datetime | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in {RunPlanStatus.COMPLETED, RunPlanStatus.BLOCKED, RunPlanStatus.FAILED}


@dataclass(frozen=True, slots=True)
class StepPolicy:
    """Step 的时间、重试及外部调用边界。"""

    timeout_seconds: int = 60
    max_attempts: int = 3
    total_deadline_seconds: int = 600
    initial_backoff_seconds: int = 5
    max_backoff_seconds: int = 60
    jitter_ratio: float = 0.2
    max_poll_attempts: int = 120
    poll_interval_seconds: int = 5
    external: bool = False

    def __post_init__(self) -> None:
        if self.timeout_seconds < 1 or self.max_attempts < 1:
            raise ValueError("workflow_step_policy_invalid")
        if self.total_deadline_seconds < self.timeout_seconds:
            raise ValueError("workflow_step_policy_invalid")
        if self.initial_backoff_seconds < 0 or self.max_backoff_seconds < self.initial_backoff_seconds:
            raise ValueError("workflow_step_backoff_invalid")
        if self.max_poll_attempts < 1 or self.poll_interval_seconds < 1:
            raise ValueError("workflow_step_poll_policy_invalid")
        if not 0 <= self.jitter_ratio <= 1:
            raise ValueError("workflow_step_backoff_invalid")


@dataclass(frozen=True, slots=True)
class StepContext:
    """运行中的 Step 可读取的统一上下文。"""

    workflow_run_id: str
    worker_id: str
    step_name: str
    input_hash: str
    idempotency_key: str
    external_request_id: str
    attempt_number: int
    max_attempts: int
    poll_number: int
    max_poll_attempts: int
    attempt_deadline_at: datetime
    total_deadline_at: datetime | None = None
    external_job_id: str | None = None
    previous_output_refs: dict[str, Any] = field(default_factory=dict)

    def remaining_timeout_seconds(self) -> int:
        """返回本次尝试剩余预算，供 HTTP/SDK 客户端设置真实超时。"""
        remaining = (self.attempt_deadline_at - datetime.now(UTC).replace(tzinfo=None)).total_seconds()
        if remaining <= 0:
            raise TimeoutError("workflow_step_timeout_budget_exhausted")
        return max(1, int(remaining))


@dataclass(frozen=True, slots=True)
class StepOutcome:
    """业务 handler 的结果；大数据只能写 Artifact，不能塞入检查点。"""

    kind: StepOutcomeKind
    output_refs: dict[str, Any] = field(default_factory=dict)
    error_category: StepErrorCategory | None = None
    error_code: str | None = None
    error_message: str | None = None
    # Workflow 可显式声明业务恢复方式；未声明时由 ErrorRecoveryPolicy 按稳定错误码推导。
    recovery_action: RecoveryAction | None = None
    retry_after_seconds: int | None = None
    external_job_id: str | None = None
    data: Any = None

    @classmethod
    def succeeded(cls, output_refs: dict[str, Any] | None = None, *, data: Any = None) -> "StepOutcome":
        return cls(StepOutcomeKind.SUCCEEDED, output_refs=output_refs or {}, data=data)

    @classmethod
    def retry_wait(
        cls, *, error_code: str, error_message: str, retry_after_seconds: int | None = None,
        error_category: StepErrorCategory = StepErrorCategory.EXTERNAL_TRANSIENT,
    ) -> "StepOutcome":
        return cls(StepOutcomeKind.RETRY_WAIT, error_category=error_category, error_code=error_code, error_message=error_message, retry_after_seconds=retry_after_seconds, recovery_action=RecoveryAction.AUTO_RETRY)

    @classmethod
    def activity_retry_wait(
        cls, *, error_code: str, error_message: str, retry_after_seconds: int,
    ) -> "StepOutcome":
        """等待 Step 内活动重试，恢复时由活动检查点复用已成功产物。"""
        return cls(
            StepOutcomeKind.ACTIVITY_RETRY_WAIT,
            error_category=StepErrorCategory.EXTERNAL_TRANSIENT,
            error_code=error_code,
            error_message=error_message,
            retry_after_seconds=retry_after_seconds,
            recovery_action=RecoveryAction.AUTO_RETRY,
        )

    @classmethod
    def waiting_external(
        cls, *, external_job_id: str, retry_after_seconds: int | None = None,
    ) -> "StepOutcome":
        if not external_job_id:
            raise ValueError("workflow_external_job_id_required")
        return cls(StepOutcomeKind.WAITING_EXTERNAL, external_job_id=external_job_id, retry_after_seconds=retry_after_seconds)

    @classmethod
    def failed(
        cls, *, error_code: str, error_message: str,
        error_category: StepErrorCategory = StepErrorCategory.INTERNAL,
        recovery_action: RecoveryAction | None = None,
    ) -> "StepOutcome":
        return cls(StepOutcomeKind.FAILED, error_category=error_category, error_code=error_code, error_message=error_message, recovery_action=recovery_action)

    @classmethod
    def blocked(
        cls, *, error_code: str, error_message: str,
        error_category: StepErrorCategory = StepErrorCategory.BUSINESS_RULE,
        recovery_action: RecoveryAction | None = None,
    ) -> "StepOutcome":
        return cls(StepOutcomeKind.BLOCKED, error_category=error_category, error_code=error_code, error_message=error_message, recovery_action=recovery_action)


StepHandler = Callable[[StepContext], StepOutcome]
StepInputFactory = Callable[[str], dict[str, Any]]
StepPersistor = Callable[[Any, StepContext, StepOutcome], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class StepDefinition:
    """业务 Workflow 声明的一个最小可恢复步骤，不包含线程、重试循环或 SQL 提交。"""

    name: str
    order: int
    handler: StepHandler
    input_factory: StepInputFactory
    policy: StepPolicy = field(default_factory=StepPolicy)
    persist_success: StepPersistor | None = None
    artifact_type: str | None = None

    def __post_init__(self) -> None:
        if not self.name.strip() or self.order < 1:
            raise ValueError("workflow_step_definition_invalid")
        if self.artifact_type is not None and not self.artifact_type.strip():
            raise ValueError("workflow_step_artifact_type_invalid")
