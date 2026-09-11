"""模型调用的纯数据上下文。

算法包只读取请求 ID 和操作名，不知道 Workflow、数据库或后端 ExternalActivity。
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from hashlib import sha256
from typing import Iterator


@dataclass(frozen=True, slots=True)
class ModelCallContext:
    root_request_id: str
    operation: str
    request_id: str


_scope: ContextVar[tuple[str, str] | None] = ContextVar("recruit_model_call_scope", default=None)


@contextmanager
def model_call_scope(root_request_id: str, operation_scope: str) -> Iterator[None]:
    """在一次 Workflow Step 的算法调用链内设置稳定的模型调用根 ID。"""
    token = _scope.set((root_request_id, operation_scope))
    try:
        yield
    finally:
        _scope.reset(token)


def current_model_call_context(*, workflow_name: str, operation: str, fingerprint: str) -> ModelCallContext | None:
    """为一次具体模型请求派生稳定 ID；非 Workflow 调用返回 None。"""
    value = _scope.get()
    if value is None:
        return None
    root_request_id, operation_scope = value
    full_operation = f"{operation_scope}:{workflow_name}:{operation}"
    request_id = sha256(f"{root_request_id}:{full_operation}:{fingerprint}".encode("utf-8")).hexdigest()
    return ModelCallContext(root_request_id=root_request_id, operation=full_operation, request_id=request_id)