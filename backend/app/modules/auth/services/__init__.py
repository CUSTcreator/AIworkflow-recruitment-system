"""授权应用服务。

服务层负责从数据库解析用户实际权限、构建访问上下文并调用 auth.domain 的纯规则。
业务模块应通过 auth.public 使用这些服务，而非自行拼接权限和部门条件。
"""

from .authorization_service import AuthorizationService
from .scope_service import ScopeService
from .field_redaction_service import FieldRedactionService

__all__ = ["AuthorizationService", "ScopeService", "FieldRedactionService"]