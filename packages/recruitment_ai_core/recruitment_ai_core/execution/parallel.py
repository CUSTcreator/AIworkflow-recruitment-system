from __future__ import annotations

import time
from contextvars import copy_context
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from typing import TypeVar, cast

from .contracts import BatchExecutionError, BatchOutcome, BatchTask, FailurePolicy


PayloadT = TypeVar("PayloadT")
ResultT = TypeVar("ResultT")


def run_bounded_tasks(
    tasks: Sequence[BatchTask[PayloadT]],
    handler: Callable[[PayloadT], ResultT],
    *,
    max_concurrency: int,
    failure_policy: FailurePolicy = "fail_fast",
    fallback: Callable[[PayloadT, Exception], ResultT] | None = None,
) -> list[BatchOutcome[ResultT]]:
    """Run independent tasks concurrently and return outcomes in input order."""
    if not tasks:
        return []
    workers = min(max(1, int(max_concurrency)), len(tasks))
    futures: list[Future[BatchOutcome[ResultT]]] = []
    with ThreadPoolExecutor(
        max_workers=workers,
        thread_name_prefix=f"stage-{tasks[0].stage}",
    ) as executor:
        for task in tasks:
            # ThreadPoolExecutor 不会自动复制 ContextVar。复制后，子任务可以继承
            # 工作流请求 ID、外部活动幂等键与日志上下文，避免运行日志变成“无归属”。
            context = copy_context()
            futures.append(
                executor.submit(context.run, _run_one, task, handler, failure_policy, fallback)
            )
        outcomes = [future.result() for future in futures]
    failed = [item for item in outcomes if item.status == "failed"]
    if failed and failure_policy in {"fail_fast", "collect_and_fail"}:
        raise BatchExecutionError(
            tasks[0].stage,
            cast(list[BatchOutcome[object]], outcomes),
        )
    return outcomes


def map_bounded(
    stage: str,
    items: Sequence[PayloadT],
    handler: Callable[[PayloadT], ResultT],
    *,
    max_concurrency: int,
    failure_policy: FailurePolicy = "fail_fast",
    fallback: Callable[[PayloadT, Exception], ResultT] | None = None,
) -> list[ResultT]:
    tasks = [
        BatchTask(task_id=f"{stage}:{index:03d}", stage=stage, payload=item)
        for index, item in enumerate(items, start=1)
    ]
    outcomes = run_bounded_tasks(
        tasks,
        handler,
        max_concurrency=max_concurrency,
        failure_policy=failure_policy,
        fallback=fallback,
    )
    return [cast(ResultT, item.result) for item in outcomes]


def run_parallel_branches(
    branches: Mapping[str, Callable[[], ResultT]],
    *,
    max_concurrency: int,
    stage: str,
) -> dict[str, ResultT]:
    names = list(branches)
    results = map_bounded(
        stage,
        names,
        lambda name: branches[name](),
        max_concurrency=max_concurrency,
    )
    return dict(zip(names, results, strict=True))


def _run_one(
    task: BatchTask[PayloadT],
    handler: Callable[[PayloadT], ResultT],
    failure_policy: FailurePolicy,
    fallback: Callable[[PayloadT, Exception], ResultT] | None,
) -> BatchOutcome[ResultT]:
    started = time.perf_counter()
    try:
        result = handler(task.payload)
        return BatchOutcome(
            task_id=task.task_id,
            stage=task.stage,
            status="completed",
            result=result,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )
    except Exception as exc:
        retryable = bool(getattr(exc, "retryable", False))
        context = getattr(exc, "context", None)
        retry_after_seconds = context.get("retry_after_seconds") if isinstance(context, dict) else getattr(exc, "retry_after_seconds", None)
        if failure_policy == "degrade_empty" and fallback is not None:
            return BatchOutcome(
                task_id=task.task_id,
                stage=task.stage,
                status="degraded",
                result=fallback(task.payload, exc),
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
                error=f"{type(exc).__name__}:{str(exc)[:300]}",
                retryable=retryable,
                retry_after_seconds=retry_after_seconds,
            )
        return BatchOutcome(
            task_id=task.task_id,
            stage=task.stage,
            status="failed",
            result=None,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
            error=f"{type(exc).__name__}:{str(exc)[:300]}",
            retryable=retryable,
            retry_after_seconds=retry_after_seconds,
        )

