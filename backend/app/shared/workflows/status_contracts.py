"""工作流运行时三层执行状态及其合法转换。

数据库仍存储字符串以兼容历史记录；所有运行时写入必须通过本模块的枚举和
转换校验，避免 Workflow、Step 与 Activity 各自维护不一致的状态集合。
"""
from __future__ import annotations

from enum import StrEnum
from typing import Mapping


class WorkflowRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    ACTIVITY_RETRY_WAIT = "activity_retry_wait"
    WAITING_EXTERNAL = "waiting_external"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    INVALIDATED = "invalidated"
    CANCELLED = "cancelled"


class ActivityExecutionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


_WORKFLOW_TRANSITIONS: Mapping[WorkflowRunStatus, frozenset[WorkflowRunStatus]] = {
    WorkflowRunStatus.PENDING: frozenset({WorkflowRunStatus.RUNNING, WorkflowRunStatus.CANCELLED}),
    WorkflowRunStatus.RUNNING: frozenset({
        WorkflowRunStatus.PENDING,
        WorkflowRunStatus.COMPLETED,
        WorkflowRunStatus.BLOCKED,
        WorkflowRunStatus.FAILED,
        WorkflowRunStatus.CANCELLED,
    }),
    WorkflowRunStatus.COMPLETED: frozenset(),
    WorkflowRunStatus.BLOCKED: frozenset({WorkflowRunStatus.PENDING, WorkflowRunStatus.CANCELLED}),
    WorkflowRunStatus.FAILED: frozenset(),
    WorkflowRunStatus.CANCELLED: frozenset(),
}

_STEP_TRANSITIONS: Mapping[StepStatus, frozenset[StepStatus]] = {
    StepStatus.PENDING: frozenset({StepStatus.RUNNING, StepStatus.INVALIDATED, StepStatus.CANCELLED}),
    StepStatus.RUNNING: frozenset({
        StepStatus.RETRY_WAIT,
        StepStatus.ACTIVITY_RETRY_WAIT,
        StepStatus.WAITING_EXTERNAL,
        StepStatus.SUCCEEDED,
        StepStatus.FAILED,
        StepStatus.BLOCKED,
        StepStatus.INVALIDATED,
        StepStatus.CANCELLED,
    }),
    StepStatus.RETRY_WAIT: frozenset({StepStatus.RUNNING, StepStatus.INVALIDATED, StepStatus.CANCELLED}),
    StepStatus.ACTIVITY_RETRY_WAIT: frozenset({StepStatus.RUNNING, StepStatus.INVALIDATED, StepStatus.CANCELLED}),
    StepStatus.WAITING_EXTERNAL: frozenset({StepStatus.RUNNING, StepStatus.INVALIDATED, StepStatus.CANCELLED}),
    StepStatus.SUCCEEDED: frozenset({StepStatus.INVALIDATED}),
    StepStatus.FAILED: frozenset({StepStatus.INVALIDATED}),
    StepStatus.BLOCKED: frozenset({StepStatus.PENDING, StepStatus.INVALIDATED, StepStatus.CANCELLED}),
    StepStatus.INVALIDATED: frozenset(),
    StepStatus.CANCELLED: frozenset(),
}

_ACTIVITY_TRANSITIONS: Mapping[ActivityExecutionStatus, frozenset[ActivityExecutionStatus]] = {
    ActivityExecutionStatus.PENDING: frozenset({ActivityExecutionStatus.RUNNING}),
    ActivityExecutionStatus.RUNNING: frozenset({
        ActivityExecutionStatus.RETRY_WAIT,
        ActivityExecutionStatus.SUCCEEDED,
        ActivityExecutionStatus.FAILED,
    }),
    ActivityExecutionStatus.RETRY_WAIT: frozenset({ActivityExecutionStatus.RUNNING}),
    ActivityExecutionStatus.SUCCEEDED: frozenset(),
    ActivityExecutionStatus.FAILED: frozenset(),
}


def ensure_workflow_transition(current: str, target: WorkflowRunStatus) -> None:
    _ensure_transition("workflow", current, target, _WORKFLOW_TRANSITIONS, WorkflowRunStatus)


def ensure_step_transition(current: str, target: StepStatus) -> None:
    _ensure_transition("step", current, target, _STEP_TRANSITIONS, StepStatus)


def ensure_activity_transition(current: str, target: ActivityExecutionStatus) -> None:
    _ensure_transition("activity", current, target, _ACTIVITY_TRANSITIONS, ActivityExecutionStatus)


def _ensure_transition(
    layer: str,
    current: str,
    target: StrEnum,
    transitions: Mapping[StrEnum, frozenset[StrEnum]],
    enum_type: type[StrEnum],
) -> None:
    try:
        current_status = enum_type(current)
    except ValueError as error:
        raise ValueError(f"workflow_{layer}_status_unknown:{current}") from error
    if current_status == target:
        return
    if target not in transitions[current_status]:
        raise ValueError(f"workflow_{layer}_transition_invalid:{current_status.value}:{target.value}")