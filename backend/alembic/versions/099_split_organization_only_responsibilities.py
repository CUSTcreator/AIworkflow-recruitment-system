"""Remove organization-only permissions from department-scoped accounts.

Revision ID: 099_split_organization_only_responsibilities
Revises: 098_reconcile_hard_screening_review_results

The responsibility catalog now separates organization-wide job-document import
and global recruitment configuration from department-scoped job editing and
screening policy. Older custom roles may still contain the former permission
codes because they were valid before the split. This data migration aligns those
stored permissions with the current contract without changing business records.
"""
from __future__ import annotations

import json

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "099_split_organization_only_responsibilities"
down_revision = "098_reconcile_hard_screening_review_results"
branch_labels = None
depends_on = None


ORGANIZATION_ONLY_PERMISSIONS = frozenset({
    "job_document.upload",
    "job_document.confirm",
    "hard_screening.catalog.manage",
    "interview_guide.manage",
})


def _permissions(value: object) -> dict[str, object]:
    if isinstance(value, str):
        return dict(json.loads(value or "{}"))
    return dict(value or {})


def upgrade() -> None:
    bind = op.get_bind()

    # A department role must not retain an organization-only permission from a
    # pre-split configuration. Preserve all unrelated permission keys exactly.
    role_rows = bind.execute(
        sa.text(
            "SELECT role_id, business_scope, permissions "
            "FROM role_definitions"
        )
    ).mappings()
    for row in role_rows:
        if row["business_scope"] == "organization":
            continue
        permissions = _permissions(row["permissions"])
        updated = {
            code: enabled
            for code, enabled in permissions.items()
            if code not in ORGANIZATION_ONLY_PERMISSIONS
        }
        if updated == permissions:
            continue
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
            {"role_id": row["role_id"], "permissions": updated},
        )

    # A department account may explicitly deny an organization-only duty, but
    # an old ``allow`` override must not re-grant it after the role cleanup.
    bind.execute(
        sa.text(
            "DELETE FROM user_permission_overrides AS override "
            "USING users AS account "
            "WHERE override.user_id = account.user_id "
            "AND account.business_scope <> 'organization' "
            "AND override.permission_code IN "
            "('job_document.upload', 'job_document.confirm', "
            "'hard_screening.catalog.manage', 'interview_guide.manage') "
            "AND override.effect = 'allow'"
        )
    )


def downgrade() -> None:
    raise RuntimeError(
        "099 删除了部门范围账号的组织级权限，无法安全自动恢复；请从备份恢复。"
    )
