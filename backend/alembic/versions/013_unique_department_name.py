"""make department names unique

Revision ID: 013_unique_department_name
Revises: 012_deduplicate_logical_jobs
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "013_unique_department_name"
down_revision = "012_deduplicate_logical_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    departments = sa.table(
        "departments",
        sa.column("department_id", sa.String),
        sa.column("name", sa.String),
    )
    duplicate = bind.execute(
        sa.select(departments.c.name)
        .group_by(departments.c.name)
        .having(sa.func.count() > 1)
    ).first()
    if duplicate is not None:
        raise RuntimeError(f"duplicate_department_name:{duplicate.name}")
    with op.batch_alter_table("departments") as batch_op:
        batch_op.create_unique_constraint("uq_departments_name", ["name"])


def downgrade() -> None:
    with op.batch_alter_table("departments") as batch_op:
        batch_op.drop_constraint("uq_departments_name", type_="unique")
