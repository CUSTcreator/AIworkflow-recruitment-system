"""HTTP 错误响应的公共 DTO。

Router 和领域服务不直接拼装此结构；所有异常统一由 ``error_handlers`` 投影，保证
前端无论面对业务拒绝、命令回滚还是未知 500 都能读取相同字段。
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ApiErrorResponse(BaseModel):
    code: str
    message: str
    retryable: bool = False
    action: Literal["none", "refresh", "retry"] = "none"
    request_id: str | None = Field(default=None, serialization_alias="requestId")
    context: dict[str, Any] = Field(default_factory=dict)

    def as_payload(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, mode="json")
