from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=512)


class AuthenticatedUserView(BaseModel):
    userId: str
    username: str
    displayName: str
    role: str
    roleId: str
    roleName: str
    departmentId: str | None = None
    businessScope: str
    permissions: list[str]
    isSystemAdmin: bool
    mustChangePassword: bool


class LoginResponse(BaseModel):
    accessToken: str
    tokenType: str = "bearer"
    user: AuthenticatedUserView
