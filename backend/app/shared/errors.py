from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
from typing import Any, Literal


Severity = Literal["warning", "error"]


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """A machine-readable structural or semantic validation problem."""

    code: str
    message: str
    field: str | None = None
    severity: Severity = "error"
    retryable: bool = False
    context: dict[str, Any] = dataclass_field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "field": self.field,
            "severity": self.severity,
            "retryable": self.retryable,
            "context": dict(self.context),
        }


class BusinessError(Exception):
    """Expected business failure independent of HTTP and FastAPI."""

    def __init__(self, code: str, message: str, *, status_code: int = 400, context: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.context = dict(context or {})


class BusinessRuleError(BusinessError):
    """HTTP-independent compatibility error for migrated service rules."""

    def __init__(
        self,
        *,
        status_code: int,
        detail: str,
        code: str = "business_rule_violation",
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(code, detail, status_code=status_code, context=context)


class ExternalServiceError(Exception):
    """Failure returned by an external API or infrastructure dependency."""

    def __init__(self, service: str, code: str, message: str, *, retryable: bool, context: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.service = service
        self.code = code
        self.message = message
        self.retryable = retryable
        self.context = dict(context or {})


def retryable_decision(error: Exception) -> bool | None:
    """Return an explicit retry decision, or None for legacy/unknown errors."""
    if isinstance(error, ExternalServiceError):
        return error.retryable
    if isinstance(error, BusinessError):
        return False
    # 算法包的并行聚合错误不能依赖后端异常类型，但会保留明确的重试结论。
    # 仅接受 bool，避免第三方异常中同名的任意真值对象改变重试策略。
    explicit = getattr(error, "retryable", None)
    if isinstance(explicit, bool):
        return explicit
    return None
