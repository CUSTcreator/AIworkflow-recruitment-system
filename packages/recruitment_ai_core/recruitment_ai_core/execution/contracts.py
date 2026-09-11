from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Literal, TypeVar


PayloadT = TypeVar("PayloadT")
ResultT = TypeVar("ResultT")
FailurePolicy = Literal["fail_fast", "collect_and_fail", "degrade_empty"]


@dataclass(frozen=True, slots=True)
class BatchTask(Generic[PayloadT]):
    task_id: str
    stage: str
    payload: PayloadT


@dataclass(slots=True)
class BatchOutcome(Generic[ResultT]):
    task_id: str
    stage: str
    status: Literal["completed", "failed", "degraded"]
    result: ResultT | None
    duration_ms: float
    error: str | None = None
    # 并行执行器不能把底层异常仅压缩成字符串；该标记供上层 StepRunner
    # 判断本检查点是否应按外部瞬态故障延迟重试。
    retryable: bool = False
    retry_after_seconds: int | None = None


class BatchExecutionError(RuntimeError):
    def __init__(self, stage: str, outcomes: list[BatchOutcome[object]]):
        self.stage = stage
        self.outcomes = outcomes
        failed_outcomes = [item for item in outcomes if item.status == "failed"]
        # 只有所有失败项都是可恢复的外部故障，才允许重试整个并行阶段；
        # 混入确定性业务/数据错误时重试不会解决问题，仍应终止当前步骤。
        self.retryable = bool(failed_outcomes) and all(item.retryable for item in failed_outcomes)
        retry_delays = [item.retry_after_seconds for item in failed_outcomes if item.retry_after_seconds is not None]
        self.retry_after_seconds = max(retry_delays) if retry_delays else None
        errors = [
            f"{item.task_id}:{item.error}"
            for item in outcomes
            if item.status == "failed"
        ]
        super().__init__(f"parallel_stage_failed:{stage}:{';'.join(errors[:5])}")
