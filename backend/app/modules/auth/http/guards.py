"""Router 可复用的授权依赖工厂。

这里适合无资源或只需组织范围的接口。Application 等资源级授权必须先加载资源，
再在 CommandRunner 或 Service 中调用 AuthorizationService，不能由全局 middleware 猜测。
"""
from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends
from sqlalchemy.orm import Session

from backend.app.db.session import get_db
from backend.app.models.entities import User
from backend.app.modules.auth.http.dependencies import get_current_user
from backend.app.modules.auth.services.authorization_service import AuthorizationService


def require_business_permission(
    permission_code: str,
    *,
    require_organization_scope: bool = False,
) -> Callable[..., User]:
    """生成 FastAPI 依赖：认证成功后要求指定通用业务权限。"""
    def guard(
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> User:
        AuthorizationService(db).require_business_action(
            user,
            permission_code,
            require_organization_scope=require_organization_scope,
        )
        return user

    return guard