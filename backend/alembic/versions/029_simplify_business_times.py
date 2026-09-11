"""simplify recruitment business times to point events

Revision ID: 029_simplify_times
Revises: 028_require_resume_time
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "029_simplify_times"
down_revision = "028_require_resume_time"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    stage_columns = {item["name"] for item in inspector.get_columns("stage_history")}
    if "effective_at" not in stage_columns:
        op.add_column("stage_history", sa.Column("effective_at", sa.DateTime(), nullable=True))
    if "effective_start_at" in stage_columns:
        op.execute(
            "UPDATE stage_history SET effective_at = effective_start_at "
            "WHERE effective_at IS NULL"
        )
    with op.batch_alter_table("stage_history") as batch:
        for name in ("effective_end_at", "effective_start_at", "time_type"):
            if name in stage_columns:
                batch.drop_column(name)

    interview_columns = {
        item["name"] for item in sa.inspect(op.get_bind()).get_columns("interviews")
    }
    with op.batch_alter_table("interviews") as batch:
        for name in (
            "business_timezone",
            "actual_end_at",
            "actual_start_at",
            "scheduled_end_at",
            "scheduled_start_at",
        ):
            if name in interview_columns:
                batch.drop_column(name)


def downgrade() -> None:
    interview_columns = {
        item["name"] for item in sa.inspect(op.get_bind()).get_columns("interviews")
    }
    for name in ("scheduled_start_at", "scheduled_end_at", "actual_start_at", "actual_end_at"):
        if name not in interview_columns:
            op.add_column("interviews", sa.Column(name, sa.DateTime(), nullable=True))
    if "business_timezone" not in interview_columns:
        op.add_column(
            "interviews",
            sa.Column("business_timezone", sa.String(64), nullable=False, server_default="Asia/Shanghai"),
        )
    stage_columns = {
        item["name"] for item in sa.inspect(op.get_bind()).get_columns("stage_history")
    }
    if "time_type" not in stage_columns:
        op.add_column(
            "stage_history",
            sa.Column("time_type", sa.String(32), nullable=False, server_default="point"),
        )
    if "effective_start_at" not in stage_columns:
        op.add_column("stage_history", sa.Column("effective_start_at", sa.DateTime(), nullable=True))
    if "effective_end_at" not in stage_columns:
        op.add_column("stage_history", sa.Column("effective_end_at", sa.DateTime(), nullable=True))
    op.execute(
        "UPDATE stage_history SET effective_start_at = effective_at "
        "WHERE effective_start_at IS NULL"
    )
    if "effective_at" in stage_columns:
        with op.batch_alter_table("stage_history") as batch:
            batch.drop_column("effective_at")
