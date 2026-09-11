"""Activity 级降级与阻塞合同。

本模块描述的是一次外部调用在自身重试耗尽后的**业务结论**，不是检查点的执行
状态。检查点仍只记录 pending/running/retry_wait/succeeded/failed；因此恢复机制
无需理解简历、岗位或评分领域。业务服务根据这里的合同决定是否能以标准模型
的保守结果继续、必须等待用户处理，或应当作为技术失败终止。
"""
from __future__ import annotations

from enum import StrEnum


class ActivityOutcomeKind(StrEnum):
    """一次 Activity 的业务可用性结论。

    ``completed`` 表示正常完成；``degraded`` 表示已按当前领域模型生成可用的保守
    结果；``blocked`` 表示缺少可靠输入，需暴露用户可执行动作。三者均是 Activity
    已结束的结论，不能与底层检查点 ``status`` 混用。
    """

    COMPLETED = "completed"
    DEGRADED = "degraded"
    BLOCKED = "blocked"


class ActivityExhaustionPolicy(StrEnum):
    """外部重试耗尽时允许的处理边界。

    该声明只决定 ActivityRunner 是否调用业务服务提供的回退处理器；具体如何构造
    WorkUnit、JDCapability 或不可用评分仍由领域服务负责，运行时不能擅自推断。
    """

    FAIL_AS_SYSTEM_ERROR = "fail_as_system_error"
    FALLBACK_TO_STANDARD_MODEL = "fallback_to_standard_model"
    BLOCK_FOR_USER_ACTION = "block_for_user_action"