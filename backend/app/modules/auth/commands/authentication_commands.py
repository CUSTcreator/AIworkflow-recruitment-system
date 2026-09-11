"""登录命令的事务适配。

本模块是 ``CommandRunner`` 对认证前场景的唯一入口。密码仅在认证服务中验证，
不得进入命令日志、审计详情或幂等请求体。
"""
from __future__ import annotations

from backend.app.core.security import create_access_token
from backend.app.infrastructure.command_runtime.command_contracts import CommandSpec
from backend.app.infrastructure.command_runtime.command_runner import CommandRunner
from backend.app.models.entities import User
from backend.app.modules.auth.service import AuthService


class AuthenticationCommands:
    """将登录成功后的最后登录时间更新纳入统一命令事务。"""

    def __init__(self, db) -> None:
        self.db = db

    def login(self, *, username: str, password: str) -> dict[str, object]:
        # 登录不能以用户名/密码作为幂等输入或日志字段；每次认证都只更新同一用户的
        # last_login_at，并由匿名认证命令的受限 spec 明确允许。
        def handler(context):
            service = AuthService(context.db)
            user = service.authenticate(username, password)
            return {
                "accessToken": create_access_token(user.user_id),
                "tokenType": "bearer",
                "user": service.user_view(user),
            }

        return CommandRunner(self.db).execute(
            spec=CommandSpec(
                action="auth.login",
                resource_type="system",
                permission_code="auth.login",
                authentication_mode="anonymous_authentication",
                idempotent=False,
                audit_exempt=True,
            ),
            user=None,
            resource_id="auth-login",
            body={},
            handler=handler,
        )
