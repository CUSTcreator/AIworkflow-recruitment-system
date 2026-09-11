"""Add department and job soft-delete fields.

Revision ID: 038_structure_soft_delete
Revises: 038_canonical_job_fields
"""

from __future__ import annotations

import json
from datetime import datetime

from alembic import op
import sqlalchemy as sa


revision = "038_structure_soft_delete"
down_revision = "038_canonical_job_fields"
branch_labels = None
depends_on = None


def _legacy_deleted_at(payload: object) -> datetime | None:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            return None
    if not isinstance(payload, dict):
        return None
    value = payload.get("deletedAt")
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def upgrade() -> None:
    with op.batch_alter_table("departments") as batch:
        batch.add_column(sa.Column("deleted_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("deleted_by_user_id", sa.String(length=64), nullable=True))
        batch.create_index("ix_departments_deleted_at", ["deleted_at"])
        batch.create_foreign_key("fk_departments_deleted_by_user", "users", ["deleted_by_user_id"], ["user_id"])
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("deleted_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("deleted_by_user_id", sa.String(length=64), nullable=True))
        batch.create_index("ix_jobs_deleted_at", ["deleted_at"])
        batch.create_foreign_key("fk_jobs_deleted_by_user", "users", ["deleted_by_user_id"], ["user_id"])

    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT job_id, payload FROM jobs")).mappings().all()
    for row in rows:
        deleted_at = _legacy_deleted_at(row["payload"])
        if deleted_at is None:
            continue
        connection.execute(
            sa.text("UPDATE jobs SET deleted_at=:deleted_at WHERE job_id=:job_id"),
            {"deleted_at": deleted_at, "job_id": row["job_id"]},
        )


def downgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.drop_constraint("fk_jobs_deleted_by_user", type_="foreignkey")
        batch.drop_index("ix_jobs_deleted_at")
        batch.drop_column("deleted_by_user_id")
        batch.drop_column("deleted_at")
    with op.batch_alter_table("departments") as batch:
        batch.drop_constraint("fk_departments_deleted_by_user", type_="foreignkey")
        batch.drop_index("ix_departments_deleted_at")
        batch.drop_column("deleted_by_user_id")
        batch.drop_column("deleted_at")