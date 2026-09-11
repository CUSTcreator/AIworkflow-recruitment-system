"""split department manager and recruiter responsibilities

Revision ID: 020_department_recruiter
Revises: 019_job_spreadsheet_departments
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "020_department_recruiter"
down_revision = "019_job_spreadsheet_departments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("jobs")}
    if "department_recruiter_id" in columns:
        return
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("department_recruiter_id", sa.String(64), nullable=True))
        batch.create_index("ix_jobs_department_recruiter_id", ["department_recruiter_id"], unique=False)
        batch.create_foreign_key(
            "fk_jobs_department_recruiter_id_users",
            "users",
            ["department_recruiter_id"],
            ["user_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.drop_constraint("fk_jobs_department_recruiter_id_users", type_="foreignkey")
        batch.drop_index("ix_jobs_department_recruiter_id")
        batch.drop_column("department_recruiter_id")
