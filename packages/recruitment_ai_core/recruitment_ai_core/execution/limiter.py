from __future__ import annotations

import time
from contextlib import contextmanager
from threading import Condition
from typing import Iterator


class GlobalConcurrencyLimiter:
    """Process-wide limiter shared by every workflow and internal executor."""

    def __init__(self) -> None:
        self._condition = Condition()
        self._active = 0

    @contextmanager
    def slot(self, limit: int) -> Iterator[float]:
        maximum = max(1, int(limit))
        started = time.perf_counter()
        with self._condition:
            while self._active >= maximum:
                self._condition.wait()
            self._active += 1
        wait_ms = round((time.perf_counter() - started) * 1000, 2)
        try:
            yield wait_ms
        finally:
            with self._condition:
                self._active -= 1
                self._condition.notify_all()


GLOBAL_LLM_LIMITER = GlobalConcurrencyLimiter()
