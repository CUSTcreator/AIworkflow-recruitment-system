"""
Workflow 的通用数据结构，例如执行上下文、步骤计划和失败处理上下文。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .step_contracts import StepDefinition


# handler 执行实际业务；transition_handler 只负责最终异常后的业务状态收尾。
# blocked_handler 专门投影“输入不足、等待用户操作”的业务状态，绝不能复用失败收尾。
WorkflowHandler = Callable[["WorkflowContext"], None]
WorkflowTransitionHandler = Callable[["WorkflowTransitionContext"], None]


@dataclass(frozen=True, slots=True)
class WorkflowContext:
    """传给业务处理器的执行身份；业务数据应由处理器按任务编号自行读取。"""

    workflow_run_id: str
    worker_id: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class WorkflowTransitionContext:
    """传给失败收尾处理器的上下文；retrying 区分“将重试”与“最终失败”。"""

    workflow_run_id: str
    worker_id: str
    error: Exception
    retrying: bool
    metadata: dict[str, Any] = field(default_factory=dict)
    recovery_action: str | None = None


@dataclass(frozen=True, slots=True)
class WorkflowSpec:
    """一个持久化任务类型的注册信息及其可恢复步骤计划。"""

    workflow_type: str
    handler: WorkflowHandler | None
    max_attempts: int = 3
    transition_handler: WorkflowTransitionHandler | None = None
    blocked_handler: WorkflowTransitionHandler | None = None
    definition_version: int = 1
    steps: tuple[StepDefinition, ...] = ()

    def __post_init__(self) -> None:
        if not self.workflow_type.strip():
            raise ValueError("workflow_type_missing")
        if self.max_attempts < 1:
            raise ValueError("workflow_max_attempts_invalid")
        if self.definition_version < 1:
            raise ValueError("workflow_definition_version_invalid")
        names = [step.name for step in self.steps]
        if len(names) != len(set(names)):
            raise ValueError("workflow_step_name_duplicate")
        if tuple(sorted(self.steps, key=lambda step: step.order)) != self.steps:
            raise ValueError("workflow_steps_not_ordered")
