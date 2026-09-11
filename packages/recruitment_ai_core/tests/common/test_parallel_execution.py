from __future__ import annotations

import threading
import time

import pytest

from recruitment_ai_core.execution import (
    BatchExecutionError,
    GlobalConcurrencyLimiter,
    map_bounded,
    run_parallel_branches,
)


def test_map_bounded_runs_concurrently_and_preserves_input_order() -> None:
    barrier = threading.Barrier(2)
    lock = threading.Lock()
    active = 0
    maximum = 0

    def handler(value: int) -> int:
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        barrier.wait(timeout=1)
        time.sleep(0.01 if value % 2 else 0.02)
        with lock:
            active -= 1
        return value * 10

    result = map_bounded(
        "ordered",
        [1, 2, 3, 4],
        handler,
        max_concurrency=2,
    )

    assert result == [10, 20, 30, 40]
    assert maximum == 2


def test_parallel_failure_policies_are_explicit() -> None:
    def handler(value: int) -> int:
        if value == 2:
            raise ValueError("bad batch")
        return value

    with pytest.raises(BatchExecutionError):
        map_bounded("critical", [1, 2, 3], handler, max_concurrency=3)

    degraded = map_bounded(
        "optional",
        [1, 2, 3],
        handler,
        max_concurrency=3,
        failure_policy="degrade_empty",
        fallback=lambda value, _error: -value,
    )
    assert degraded == [1, -2, 3]


def test_parallel_branches_return_results_by_name() -> None:
    barrier = threading.Barrier(2)
    result = run_parallel_branches(
        {
            "left": lambda: (barrier.wait(timeout=1), "L")[1],
            "right": lambda: (barrier.wait(timeout=1), "R")[1],
        },
        max_concurrency=2,
        stage="branches",
    )
    assert result == {"left": "L", "right": "R"}


def test_global_limiter_caps_nested_parallel_calls() -> None:
    limiter = GlobalConcurrencyLimiter()
    lock = threading.Lock()
    active = 0
    maximum = 0

    def call(value: int) -> int:
        nonlocal active, maximum
        with limiter.slot(2):
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.02)
            with lock:
                active -= 1
        return value

    assert map_bounded("limited", [1, 2, 3, 4], call, max_concurrency=4) == [
        1,
        2,
        3,
        4,
    ]
    assert maximum == 2


def test_parallel_error_preserves_transient_retry_metadata() -> None:
    """并行评分中的供应商瞬时错误必须让上层检查点重试。"""

    class TemporaryProviderError(RuntimeError):
        retryable = True
        context = {"retry_after_seconds": 7}

    with pytest.raises(BatchExecutionError) as caught:
        map_bounded(
            "provider_call",
            [1, 2],
            lambda value: (_ for _ in ()).throw(TemporaryProviderError("connection refused")) if value == 2 else value,
            max_concurrency=2,
        )

    assert caught.value.retryable is True
    assert caught.value.retry_after_seconds == 7


def test_parallel_error_is_not_retryable_when_failure_causes_are_mixed() -> None:
    """确定性数据错误与瞬时错误并存时不能盲目重跑整批。"""

    class TemporaryProviderError(RuntimeError):
        retryable = True

    def handler(value: int) -> int:
        if value == 1:
            raise TemporaryProviderError("connection refused")
        raise ValueError("invalid input")

    with pytest.raises(BatchExecutionError) as caught:
        map_bounded("mixed", [1, 2], handler, max_concurrency=2)

    assert caught.value.retryable is False