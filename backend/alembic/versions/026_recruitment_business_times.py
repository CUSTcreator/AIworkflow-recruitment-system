"""add recruitment business times

Revision ID: 026_recruitment_times
Revises: 025_business_permissions
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "026_recruitment_times"
down_revision = "025_business_permissions"
branch_labels = None
depends_on = None


def _add_column(table: str, column: sa.Column) -> None:
    existing = {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}
    if column.name not in existing:
        op.add_column(table, column)


def upgrade() -> None:
    _add_column("resume_submissions", sa.Column("business_submitted_at", sa.DateTime(), nullable=True))
    op.execute("UPDATE resume_submissions SET business_submitted_at = created_at WHERE business_submitted_at IS NULL")
    for name in ("scheduled_start_at", "scheduled_end_at", "actual_start_at", "actual_end_at"):
        _add_column("interviews", sa.Column(name, sa.DateTime(), nullable=True))
    _add_column("interviews", sa.Column("business_timezone", sa.String(64), nullable=False, server_default="Asia/Shanghai"))
    _add_column("stage_history", sa.Column("effective_start_at", sa.DateTime(), nullable=True))
    _add_column("stage_history", sa.Column("effective_end_at", sa.DateTime(), nullable=True))
    _add_column("stage_history", sa.Column("time_type", sa.String(32), nullable=False, server_default="system"))
    _add_column("stage_history", sa.Column("business_timezone", sa.String(64), nullable=False, server_default="Asia/Shanghai"))
    _add_column("human_decisions", sa.Column("effective_at", sa.DateTime(), nullable=True))
    _add_column("human_decisions", sa.Column("business_timezone", sa.String(64), nullable=False, server_default="Asia/Shanghai"))


def downgrade() -> None:
    for table, columns in (
        ("human_decisions", ("business_timezone", "effective_at")),
        ("stage_history", ("business_timezone", "time_type", "effective_end_at", "effective_start_at")),
        ("interviews", ("business_timezone", "actual_end_at", "actual_start_at", "scheduled_end_at", "scheduled_start_at")),
    ):
        existing = {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}
        for name in columns:
            if name in existing:
                op.drop_column(table, name)
    if "business_submitted_at" in {item["name"] for item in sa.inspect(op.get_bind()).get_columns("resume_submissions")}:
        op.drop_column("resume_submissions", "business_submitted_at")
