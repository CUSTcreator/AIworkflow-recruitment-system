"""移除将数据可见性错误编码为职责权限的历史权限码。"""
from __future__ import annotations

import json

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "103_remove_candidate_view_permission"
down_revision = "102_stage_scoped_recruitment_permissions"
branch_labels = None
depends_on = None


RETIRED_PERMISSION_CODE = "candidate.view"


def _permissions(value: object) -> dict[str, object]:
    """将数据库 JSON 值转换为可安全修改的权限字典。"""
    if isinstance(value, str):
        return dict(json.loads(value or "{}"))
    return dict(value or {})


def _without_candidate_view(value: object) -> dict[str, object]:
    """保留其他角色职责，仅移除不再参与授权的历史查看码。"""
    permissions = _permissions(value)
    permissions.pop(RETIRED_PERMISSION_CODE, None)
    return permissions


def upgrade() -> None:
    """清理角色默认权限和账号例外，不改动任何业务数据或表结构。"""
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT role_id, permissions FROM role_definitions")
    ).mappings()
    for row in rows:
        bind.execute(
            sa.text(
                "UPDATE role_definitions SET permissions = :permissions "
                "WHERE role_id = :role_id"
            ).bindparams(
                sa.bindparam(
                    "permissions",
                    type_=sa.JSON().with_variant(JSONB(), "postgresql"),
                )
            ),
            {
                "role_id": row["role_id"],
                "permissions": _without_candidate_view(row["permissions"]),
            },
        )
    bind.execute(
        sa.text(
            "DELETE FROM user_permission_overrides "
            "WHERE permission_code = :permission_code"
        ),
        {"permission_code": RETIRED_PERMISSION_CODE},
    )


def downgrade() -> None:
    raise RuntimeError(
        "103 已删除无效查看权限的个人例外，无法在不猜测历史配置意图的情况下自动降级；请从备份恢复。"
    )
