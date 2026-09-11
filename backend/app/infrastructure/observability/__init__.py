"""可观测性公共入口。

本模块统一三类数据的边界：
1. ``log_event``：HTTP、Worker、外部调用、StepRunner、数据库适配器都写 JSON
   stdout，交由 Docker/日志平台采集；这里不直接写日志数据库。
2. ``record_audit_event``：业务命令明确调用时写 ``audit_events``，回答“谁改了什么”。
3. ``WorkflowExecutionEventWriter``：仅由 StepRunner 在步骤状态事务中写
   ``workflow_execution_events``，供任务中心展示；基础设施适配器不得直接写它。

新增日志时应优先使用 ``log_event`` 并传稳定 ID/错误码，不能传简历原文、令牌、
提示词、SQL 参数或外部服务原始回包。
"""

from .logging import configure_logging, get_logger, log_event
from .activity_contracts import ActivityEvent, ActivityType
from .persistence_errors import describe_persistence_error

__all__ = ["configure_logging", "get_logger", "log_event", "ActivityEvent", "ActivityType", "describe_persistence_error"]