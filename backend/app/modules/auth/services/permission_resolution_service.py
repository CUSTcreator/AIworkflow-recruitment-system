"""角色默认权限与用户覆盖权限的数据库解析服务。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.entities import RoleDefinition, User, UserPermissionOverride
from backend.app.modules.auth.domain.permission_catalog import PERMISSION_LABELS, ROLE_DEFAULT_PERMISSIONS


class PermissionResolutionService:
    """解析 User 的有效原子权限；不负责部门范围或资源授权。

    角色表仍保存原子权限码，以兼容既有数据库和接口。新管理端提交的职责包
    已在 AccountAdminService 写入边界展开，因此这里继续只做“角色默认权限 +
    个人 allow/deny 覆盖”的确定性合并。
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    def permission_overrides(self, user_id: str) -> dict[str, str]:
        """读取单用户 allow/deny 覆盖，未知权限不会进入有效权限集合。"""
        return {
            row.permission_code: row.effect
            for row in self.db.scalars(
                select(UserPermissionOverride).where(UserPermissionOverride.user_id == user_id)
            )
        }

    def effective_permissions(self, user: User) -> set[str]:
        """按“角色默认权限 + allow 覆盖 - deny 覆盖”计算最终权限。

        有效权限只包含职责包展开后的敏感业务操作。详情、附件、审计轨迹和
        纯恢复不依赖原子权限，由 AuthorizationService 按账号有效状态和具体
        资源的数据范围判断。
        """
        role = self.db.get(RoleDefinition, user.role_definition_id) if user.role_definition_id else None
        permissions = (
            {
                code
                for code, enabled in (role.permissions or {}).items()
                if enabled and code in PERMISSION_LABELS
            }
            if role is not None and role.is_active
            else set(ROLE_DEFAULT_PERMISSIONS.get(user.role, set()))
        )
        for code, effect in self.permission_overrides(user.user_id).items():
            if code not in PERMISSION_LABELS:
                continue
            if effect == "allow":
                permissions.add(code)
            elif effect == "deny":
                permissions.discard(code)
        return permissions
