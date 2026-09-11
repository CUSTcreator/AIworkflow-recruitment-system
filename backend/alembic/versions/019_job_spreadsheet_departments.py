"""support per-row department resolution for job spreadsheets

Revision ID: 019_job_spreadsheet_departments
Revises: 018_screening_failure
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "019_job_spreadsheet_departments"
down_revision = "018_screening_failure"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"]: column for column in sa.inspect(bind).get_columns("job_drafts")}
    if "source_department_name" not in columns:
        op.add_column(
            "job_drafts",
            sa.Column("source_department_name", sa.String(255), nullable=True),
        )
    if "department_match_status" not in columns:
        op.add_column(
            "job_drafts",
            sa.Column(
                "department_match_status",
                sa.String(32),
                nullable=False,
                server_default="matched",
            ),
        )

    columns = {column["name"]: column for column in sa.inspect(bind).get_columns("job_drafts")}
    if columns["department_id"]["nullable"]:
        return
    if bind.dialect.name == "sqlite":
        existing = sa.Table("job_drafts", sa.MetaData(), autoload_with=bind)
        with op.batch_alter_table("job_drafts", copy_from=existing) as batch:
            batch.alter_column(
                "department_id",
                existing_type=sa.String(64),
                nullable=True,
            )
    else:
        op.alter_column(
            "job_drafts",
            "department_id",
            existing_type=sa.String(64),
            nullable=True,
        )


def downgrade() -> None:
    op.execute("DELETE FROM job_drafts WHERE department_id IS NULL")
    with op.batch_alter_table("job_drafts") as batch:
        batch.drop_column("department_match_status")
        batch.drop_column("source_department_name")
        batch.alter_column(
            "department_id",
            existing_type=sa.String(64),
            nullable=False,
        )
