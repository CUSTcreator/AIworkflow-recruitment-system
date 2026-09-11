"""外部 Activity 的统一可观测性合同。

LLM、MinerU、MinIO 和普通 HTTP 都遵循同一组公共事件字段；不同类型只在
``diagnostics`` 中补充自己的安全字段。该合同只描述技术事实，不承载业务正文。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ActivityType(StrEnum):
    """外部调用的稳定分类；数据库调用由数据库适配器单独观测。"""

    LLM = "llm"
    MINERU = "mineru"
    MINIO = "minio"
    HTTP = "http"
    DATABASE = "database"


@dataclass(frozen=True, slots=True)
class ActivityEvent:
    """一次 Activity 生命周期事件的内存表示。

    ``diagnostics`` 只能放脱敏后的类型专属字段，最终由事件合同过滤后写入
    stdout 或 WorkflowExecutionEvent，不能直接放原始回包、Prompt 或 SQL 参数。
    """

    event_type: str
    activity_type: ActivityType | str
    service: str
    operation: str
    workflow_run_id: str | None = None
    step_name: str | None = None
    activity_key: str | None = None
    attempt: int | None = None
    duration_ms: float | None = None
    error_category: str | None = None
    error_code: str | None = None
    retryable: bool | None = None
    degraded: bool | None = None
    external_request_id: str | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)
