"""外部调用错误的统一分类规则。

本模块只把供应商或网络异常转换为稳定的 ``ExternalServiceError``；是否等待、
重试或终止始终由 StepRunner 决定。适配器应保留原始异常或使用
``ExternalHttpError`` 携带 HTTP 状态与 Retry-After，不能把网络错误抹成普通
``RuntimeError``。
"""
from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
import http.client
import re
from typing import Any, Mapping
import urllib.error

import httpx

from backend.app.shared.errors import ExternalServiceError


class ExternalHttpError(RuntimeError):
    """适配器遇到非 2xx 响应时保留的结构化错误。"""

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds


def retry_after_seconds_from_headers(headers: Mapping[str, Any] | None) -> int | None:
    """解析供应商 Retry-After；无效值不阻塞既定的 Step 指数退避。"""
    if not headers:
        return None
    raw = headers.get("Retry-After") or headers.get("retry-after")
    if raw is None:
        return None
    value = str(raw).strip()
    if value.isdigit():
        return max(1, min(int(value), 3600))
    try:
        retry_at = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=UTC)
    seconds = int((retry_at - datetime.now(UTC)).total_seconds())
    return max(1, min(seconds, 3600))


def classify_external_error(service: str, operation: str, error: Exception) -> ExternalServiceError:
    """规范化传输、限流和供应商异常，并保留可由 StepRunner 使用的退避建议。"""
    if isinstance(error, ExternalServiceError):
        return error
    related = tuple(_exception_chain(error))
    status_code = next((value for item in related if (value := _status_code(item)) is not None), None)
    retry_after_seconds = next((value for item in related if (value := _retry_after_seconds(item)) is not None), None)
    message = " | ".join(str(item) or type(item).__name__ for item in related)
    normalized = message.lower()
    # 结构化状态码优先；保留历史适配器只在错误文本中携带 429/5xx 的兼容路径。
    legacy_retryable_status = bool(re.search(r"(?<!\d)(?:408|425|429|5\d{2})(?!\d)", normalized))
    retryable_status = (
        status_code in {408, 425, 429}
        or (status_code is not None and status_code >= 500)
        or legacy_retryable_status
    )
    retryable_transport = any(
        isinstance(
            item,
            (
                TimeoutError,
                ConnectionError,
                OSError,
                urllib.error.URLError,
                httpx.TimeoutException,
                httpx.TransportError,
                http.client.IncompleteRead,
            ),
        )
        for item in related
    )
    certificate_failure = any(marker in normalized for marker in (
        "certificate verify failed", "certificate_verify_failed", "hostname mismatch",
    ))
    # 鉴权、模型、参数、上下文超限和安全拒答属于永久错误；即使文本中偶然出现 timeout，也不得重试。
    permanent_message = any(marker in normalized for marker in (
        "authentication", "unauthorized", "invalid api key", "api key", "model not found",
        "context length", "maximum context", "too many tokens", "invalid request", "bad request",
        "content policy", "safety", "refusal", "refused", "unsupported parameter",
    ))
    retryable_message = any(marker in normalized for marker in (
        "timeout", "timed out", "connection", "temporar", "rate limit",
        "unexpected_eof", "eof occurred in violation", "remoteprotocolerror", "incompleteread",
        "connection reset", "connection aborted", "tlsv1 alert internal error",
    ))
    retryable = not permanent_message and not certificate_failure and (retryable_status or retryable_transport or retryable_message)
    code_suffix = "transient" if retryable else "failed"
    context: dict[str, Any] = {}
    if status_code is not None:
        context["status_code"] = status_code
    if retry_after_seconds is not None:
        context["retry_after_seconds"] = retry_after_seconds
    return ExternalServiceError(
        service,
        f"{operation}_{code_suffix}",
        message,
        retryable=retryable,
        context=context,
    )


def _exception_chain(error: Exception):
    """遍历显式异常链，避免适配器包装后丢失底层 HTTP/TLS 错误类型。"""
    seen: set[int] = set()
    current: BaseException | None = error
    while isinstance(current, Exception) and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _status_code(error: Exception) -> int | None:
    raw = getattr(error, "status_code", None)
    if isinstance(raw, int):
        return raw
    response = getattr(error, "response", None)
    raw = getattr(response, "status_code", None)
    if isinstance(raw, int):
        return raw
    raw = getattr(error, "code", None)
    return raw if isinstance(raw, int) else None


def _retry_after_seconds(error: Exception) -> int | None:
    value = getattr(error, "retry_after_seconds", None)
    if isinstance(value, int) and value > 0:
        return value
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None) or getattr(error, "headers", None)
    return retry_after_seconds_from_headers(headers)
