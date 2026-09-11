"""Application 写命令的兼容适配器。

旧调用方仍传入 ``handler(user, application, body)``；本类将其转换为 CommandRunner 的
CommandSpec，使 Application 写操作先经过统一授权、幂等和事务边界。待所有调用方迁移
为直接声明 CommandSpec 后，本文件可以删除。
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from backend.app.infrastructure.command_runtime import CommandRunner, CommandSpec
from backend.app.infrastructure.command_runtime.command_contracts import ResourceLoader
from backend.app.models.entities import Application, User
from backend.app.modules.auth.public import ACTION_PERMISSIONS


class ApplicationCommandExecutor:
    """旧 Application 命令签名到通用 CommandRunner 的过渡适配器。"""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.runner = CommandRunner(db)

    def execute(
        self,
        *,
        user: User,
        idempotency_key: str,
        action: str,
        application_id: str,
        body: dict[str, Any] | None,
        handler: Callable[[User, Application, dict[str, Any]], dict[str, Any]],
        authorization_action: str | None = None,
        resource_loader: ResourceLoader | None = None,
    ) -> dict[str, Any]:
        """以历史 Application 请求指纹执行，并在业务 handler 前统一完成授权。"""
        actual_action = authorization_action or action
        permission_code = ACTION_PERMISSIONS[actual_action]
        spec = CommandSpec(
            action=action,
            authorization_action=actual_action,
            resource_type="application",
            permission_code=permission_code,
            idempotency_resource_key="application_id",
        )
        return self.runner.execute(
            spec=spec,
            user=user,
            resource_id=application_id,
            body=body,
            idempotency_key=idempotency_key,
            resource_loader=resource_loader,
            handler=lambda context: handler(context.user, context.resource, context.body),
        )