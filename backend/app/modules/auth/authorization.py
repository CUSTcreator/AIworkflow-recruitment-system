"""旧授权 API 的兼容门面。

权限目录、纯策略和数据库解析已经迁入 ``auth.domain`` / ``auth.services``。
本文件暂时保留既有函数签名，供尚未迁移的业务模块调用；新增代码应通过
``backend.app.modules.auth.public`` 使用 AuthorizationService。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.models.entities import Application, User
from backend.app.modules.auth.domain.permission_catalog import (
    ACTION_PERMISSIONS,
    FIRST_INTERVIEW_ACTIONS,
    PERMISSION_DEFINITIONS,
    PERMISSION_LABELS,
    PERMISSION_REQUIRED_BUSINESS_SCOPE,
    RESPONSIBILITY_BUNDLES,
    ROLE_DEFAULT_RESPONSIBILITY_BUNDLES,
    ROLE_DEFAULT_PERMISSIONS,
    SECOND_INTERVIEW_ACTIONS,
    canonical_responsibility_bundle_code,
    canonical_responsibility_bundle_codes,
    expand_responsibility_bundles,
)
from backend.app.modules.auth.services.authorization_service import AuthorizationService
from backend.app.modules.auth.services.permission_resolution_service import PermissionResolutionService


def permission_overrides(db: Session, user_id: str) -> dict[str, str]:
    """兼容旧调用：读取用户级 allow/deny 覆盖。"""
    return PermissionResolutionService(db).permission_overrides(user_id)


def effective_permissions(db: Session, user: User) -> set[str]:
    """兼容旧调用：计算角色权限和用户覆盖后的有效权限。"""
    return PermissionResolutionService(db).effective_permissions(user)


def has_permission(db: Session, user: User, permission_code: str) -> bool:
    """兼容旧调用：只判断账号状态和权限码，不含资源数据范围。"""
    return permission_code in AuthorizationService(db).access_context(user).permissions and bool(
        user.is_active and getattr(user, "deleted_at", None) is None
    )


def scope_allows(user: User, department_id: str | None) -> bool:
    """兼容旧调用：判断部门或全公司数据范围。"""
    if user.business_scope == "organization":
        return True
    return bool(department_id and user.department_id and department_id == user.department_id)


def can_business_action(
    db: Session,
    user: User,
    permission_code: str,
    *,
    department_id: str | None = None,
    require_organization_scope: bool = False,
) -> bool:
    """兼容旧调用：委托新的 AuthorizationService。"""
    return AuthorizationService(db).can_business_action(
        user,
        permission_code,
        department_id=department_id,
        require_organization_scope=require_organization_scope,
    )


def assert_business_action(
    db: Session,
    user: User,
    permission_code: str,
    *,
    department_id: str | None = None,
    require_organization_scope: bool = False,
) -> None:
    """兼容旧调用：要求通用业务授权。"""
    AuthorizationService(db).require_business_action(
        user,
        permission_code,
        department_id=department_id,
        require_organization_scope=require_organization_scope,
    )


def assert_permission(db: Session, user: User, application: Application, action: str) -> None:
    """兼容旧调用：要求 Application 动作授权与负责人限制。"""
    AuthorizationService(db).require_application_action(user, application, action)
