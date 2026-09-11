"""认证 HTTP 适配层：处理登录与当前用户信息请求。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.modules.auth.http.dependencies import get_current_user
from backend.app.db.session import get_db
from backend.app.models.entities import User
from backend.app.modules.auth.schemas import LoginRequest, LoginResponse
from backend.app.modules.auth.commands.authentication_commands import AuthenticationCommands
from backend.app.modules.auth.service import AuthService


router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)):
    # 登录是唯一允许认证前主体的同步命令；令牌不参与命令日志和幂等账本。
    return AuthenticationCommands(db).login(username=body.username, password=body.password)


@router.get("/me")
def me(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return AuthService(db).user_view(user)
