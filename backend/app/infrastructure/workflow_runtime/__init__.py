"""持久化工作流运行时入口。

本包实现队列领取、业务 Step 检查点、Step 内活动恢复和 Artifact；业务模块只能声明
StepDefinition，不能直接修改运行时检查点状态。
"""
from .activity_runner import (
    ActivityBatchResult,
    ActivityContext,
    ActivityDefinition,
    ActivityPolicy,
    ActivityRunner,
    ActivityResolution,
    is_safe_model_degradation_error,
)
from .external_activity import (
    ExternalActivity,
    ExternalActivityContext,
    ExternalActivityResult,
    ExternalActivityStatus,
    current_external_activity_context,
    external_activity_scope,
)
from .queue import WorkflowQueue
from .runner import WorkflowRunner
from .runtime import WorkflowRuntime
from .step_runner import StepRunner

__all__ = [
    "ActivityBatchResult", "ActivityContext", "ActivityDefinition", "ActivityPolicy", "ActivityRunner", "ActivityResolution", "is_safe_model_degradation_error",
    "ExternalActivity", "ExternalActivityContext", "ExternalActivityResult", "ExternalActivityStatus",
    "current_external_activity_context", "external_activity_scope",
    "WorkflowQueue", "WorkflowRunner", "WorkflowRuntime", "StepRunner",
]
