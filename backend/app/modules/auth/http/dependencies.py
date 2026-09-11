from __future__ import annotations

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from backend.app.db.session import get_db
from backend.app.core.security import decode_access_token
from backend.app.models.entities import User
from backend.app.modules.auth.service import AuthService
from backend.app.modules.auth.services.authorization_service import AuthorizationService


def get_current_user(
    authorization: str | None = Header(default=None, alias="Authorization"),
    db: Session = Depends(get_db),
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="请先登录")
    payload = decode_access_token(authorization.removeprefix("Bearer ").strip())
    user_id = payload.get("sub") if payload else None
    if not isinstance(user_id, str):
        raise HTTPException(status_code=401, detail="登录状态已失效，请重新登录")
    user = AuthService(db).get_user(user_id)
    if user.deleted_at is not None:
        raise HTTPException(status_code=403, detail="账号已被删除")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="账号已被禁用")
    return user


def require_system_admin(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    AuthorizationService(db).require_system_admin(user)
    return user
