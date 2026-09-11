"""统一的数据范围服务。

本阶段先提供可复用的范围对象；下一步迁移 Candidate、Application、Job 读模型时，
由各查询仓储把该对象转换为 SQL 条件，避免 auth 模块反向依赖业务 ORM 实体。
"""
from __future__ import annotations

from dataclasses import dataclass

from backend.app.models.entities import User
from backend.app.modules.auth.services.authorization_service import AuthorizationService


@dataclass(frozen=True, slots=True)
class DataScope:
    """一个主体可访问的组织或单部门范围。"""

    business_scope: str
    department_id: str | None

    def allows_department(self, department_id: str | None) -> bool:
        """供查询构建器和资源访问服务复用部门范围判断。"""
        return self.business_scope == "organization" or bool(
            department_id and self.department_id and department_id == self.department_id
        )


class ScopeService:
    """从授权上下文生成查询可消费的数据范围，不直接执行 SQL。

    列表、导出和统计的查询构建器必须在分页、计数或聚合前使用此范围添加 SQL
    条件；仅在详情接口校验范围会泄露列表条目或统计数字。
    """

    def __init__(self, authorization: AuthorizationService) -> None:
        self.authorization = authorization

    def data_scope(self, user: User) -> DataScope:
        """为当前用户生成不可变范围快照。"""
        context = self.authorization.access_context(user)
        return DataScope(context.business_scope, context.department_id)
