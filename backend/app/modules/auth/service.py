from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.security import verify_password
from backend.app.models.entities import RoleDefinition, User
from backend.app.modules.auth.authorization import effective_permissions
from backend.app.shared.errors import BusinessError


class AuthService:
    """Authentication and current-user queries independent of HTTP."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def get_user(self, user_id: str) -> User:
        user = self.db.get(User, user_id)
        if user is None or user.deleted_at is not None:
            raise BusinessError("user_not_found", "用户不存在", status_code=401)
        return user

    def authenticate(self, username: str, password: str) -> User:
        normalized_username = username.strip().lower()
        user = self.db.execute(
            select(User).where(func.lower(User.username) == normalized_username)
        ).scalar_one_or_none()
        if user is None or user.deleted_at is not None or not user.is_active or not verify_password(password, user.password_hash):
            raise BusinessError("invalid_credentials", "用户名或密码错误", status_code=401)
        # 最后登录时间由外层 CommandRunner 与登录响应原子提交；认证服务不得自行 commit。
        user.last_login_at = datetime.now(UTC).replace(tzinfo=None)
        return user

    def user_view(self, user: User) -> dict[str, object]:
        role_definition = (
            self.db.get(RoleDefinition, user.role_definition_id)
            if user.role_definition_id
            else None
        )
        return {
            "userId": user.user_id,
            "username": user.username,
            "displayName": user.display_name,
            "role": user.role,
            "roleId": role_definition.role_id if role_definition else user.role,
            "roleName": role_definition.name if role_definition else user.role,
            "departmentId": user.department_id,
            "businessScope": user.business_scope,
            "permissions": sorted(effective_permissions(self.db, user)),
            "isSystemAdmin": user.is_system_admin,
            "mustChangePassword": user.must_change_password,
        }
