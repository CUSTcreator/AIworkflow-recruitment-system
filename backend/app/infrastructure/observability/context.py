from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator


_context: ContextVar[dict[str, str]] = ContextVar("observability_context", default={})


def current_context() -> dict[str, str]:
    return dict(_context.get())


@contextmanager
def bind_context(**values: str | None) -> Iterator[None]:
    """Temporarily bind correlation fields to all logs in this execution scope."""
    merged = current_context()
    merged.update({key: str(value) for key, value in values.items() if value is not None})
    token = _context.set(merged)
    try:
        yield
    finally:
        _context.reset(token)


def request_id() -> str | None:
    return current_context().get("request_id")
