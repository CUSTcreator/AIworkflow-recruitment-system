"""工作流步骤执行预算的无侵入传递。"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from math import ceil
from time import monotonic
from typing import Iterator

_execution_deadline: ContextVar[float | None] = ContextVar(
    "recruit_execution_deadline", default=None
)


@contextmanager
def execution_timeout_budget(timeout_seconds: int) -> Iterator[None]:
    """在当前调用链内设置总时间预算，供 LLM 等外部适配器自动读取。"""
    token = _execution_deadline.set(monotonic() + max(1, timeout_seconds))
    try:
        yield
    finally:
        _execution_deadline.reset(token)


def remaining_execution_timeout_seconds() -> int | None:
    """返回当前调用链的剩余时间；未处于 Workflow Step 时返回 ``None``。"""
    deadline = _execution_deadline.get()
    if deadline is None:
        return None
    return max(0, ceil(deadline - monotonic()))