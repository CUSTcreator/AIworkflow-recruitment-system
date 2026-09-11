"""工作流共享合同入口。

业务模块只从这里导入 Workflow/Step 合同；队列、数据库检查点和 Worker 实现位于
``infrastructure.workflow_runtime``，防止业务编排反向依赖基础设施细节。
"""
from .contracts import WorkflowContext, WorkflowHandler, WorkflowSpec, WorkflowTransitionContext
from .activity_contracts import ActivityExhaustionPolicy, ActivityOutcomeKind
from .status_contracts import ActivityExecutionStatus, WorkflowRunStatus
from .registry import WorkflowRegistry
from .step_contracts import (
    RecoveryAction,
    RunPlanResult,
    RunPlanStatus,
    StepContext,
    StepDefinition,
    StepErrorCategory,
    StepOutcome,
    StepOutcomeKind,
    StepPolicy,
    StepStatus,
)

__all__ = [
    "WorkflowContext", "WorkflowHandler", "WorkflowRegistry", "WorkflowSpec", "WorkflowTransitionContext",
    "ActivityExhaustionPolicy", "ActivityExecutionStatus", "ActivityOutcomeKind", "WorkflowRunStatus",
    "RecoveryAction", "RunPlanResult", "RunPlanStatus", "StepContext", "StepDefinition", "StepErrorCategory",
    "StepOutcome", "StepOutcomeKind", "StepPolicy", "StepStatus",
]