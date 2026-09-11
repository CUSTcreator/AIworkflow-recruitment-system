"""backfill HR system-admin permission

Revision ID: 068_hr_system_admin_perm
Revises: 067_candidate_intake_reasons
"""
from __future__ import annotations

import json

from alembic import op
import sqlalchemy as sa

from backend.app.models.entities import JsonType


revision = "068_hr_system_admin_perm"
down_revision = "067_candidate_intake_reasons"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """为既有内置 HR 角色补齐显式系统管理权限。"""
    roles = sa.table(
        "role_definitions",
        sa.column("role_id", sa.String(length=64)),
        sa.column("permissions", JsonType),
    )
    bind = op.get_bind()
    permissions = bind.execute(
        sa.select(roles.c.permissions).where(roles.c.role_id == "hr")
    ).scalar_one_or_none()
    if permissions is None:
        return
    if isinstance(permissions, str):
        permissions = json.loads(permissions or "{}")
    updated = {**dict(permissions or {}), "system.admin": True}
    bind.execute(
        roles.update().where(roles.c.role_id == "hr").values(permissions=updated)
    )


def downgrade() -> None:
    # 这是不可逆的数据修复：不能在降级时猜测该权限是否由人工授予。
    pass
