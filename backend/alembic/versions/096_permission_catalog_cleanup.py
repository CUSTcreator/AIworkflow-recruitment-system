"""Clean up retired permissions and seed recruitment-settings permissions.

Revision ID: 096_permission_catalog_cleanup
Revises: 095_reconcile_document_import_schema
"""
from __future__ import annotations

import json

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "096_permission_catalog_cleanup"
down_revision = "095_reconcile_document_import_schema"
branch_labels = None
depends_on = None


def _permissions(value: object) -> dict[str, object]:
    if isinstance(value, str):
        return dict(json.loads(value))
    return dict(value or {})


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT role_id, permissions FROM role_definitions")
    ).mappings()
    for row in rows:
        permissions = _permissions(row["permissions"])
        permissions.pop("candidate.delete", None)
        permissions.pop("system.admin", None)
        if row["role_id"] == "hr":
            permissions["hard_screening.catalog.manage"] = True
            permissions["interview_guide.manage"] = True
        bind.execute(
            sa.text(
                "UPDATE role_definitions SET permissions = :permissions WHERE role_id = :role_id"
            ).bindparams(
                sa.bindparam(
                    "permissions",
                    type_=sa.JSON().with_variant(JSONB(), "postgresql"),
                )
            ),
            {"role_id": row["role_id"], "permissions": permissions},
        )
    bind.execute(
        sa.text(
            "DELETE FROM user_permission_overrides "
            "WHERE permission_code IN ('candidate.delete', 'system.admin')"
        )
    )


def downgrade() -> None:
    raise RuntimeError(
        "096 删除了已失效的 candidate.delete 和 system.admin 权限，无法安全自动恢复；请从备份恢复。"
    )
