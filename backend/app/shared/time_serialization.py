"""面向 HTTP DTO 的 UTC 时间序列化。

数据库中的运行时间为无时区 datetime，但其语义统一是 UTC。直接调用
``datetime.isoformat()`` 会丢失该语义，浏览器会将其误判为本地时间。所有
Workflow 面向前端的时间字段都必须通过本模块输出带 ``Z`` 的 ISO-8601 文本。
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def utc_iso(value: datetime | None) -> str:
    """将 UTC 语义的 datetime 输出为带 ``Z`` 的 ISO-8601 字符串。"""
    if value is None:
        return ""
    utc_value = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return utc_value.isoformat().replace("+00:00", "Z")


def normalize_utc_iso(value: Any) -> str:
    """规范化检查点 JSON 中的调度时间；非时间值保持为空或原文本。"""
    if isinstance(value, datetime):
        return utc_iso(value)
    if not isinstance(value, str) or not value:
        return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    return utc_iso(parsed)
