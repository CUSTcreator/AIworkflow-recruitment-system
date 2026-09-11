"""add workflow activity checkpoints

Revision ID: 073_activity_checkpoints
Revises: 072_interview_record_nullable
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "073_activity_checkpoints"
down_revision = "072_interview_record_nullable"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建 Step 内活动恢复账本；它是运行时基础设施，不迁移业务领域数据。"""
    inspector = sa.inspect(op.get_bind())
    if "workflow_activity_checkpoints" in set(inspector.get_table_names()):
        return
    op.create_table(
        "workflow_activity_checkpoints",
        sa.Column("activity_checkpoint_id", sa.String(length=64), primary_key=True),
        sa.Column("workflow_run_id", sa.String(length=64), nullable=False),
        sa.Column("parent_step_name", sa.String(length=96), nullable=False),
        sa.Column("activity_key", sa.String(length=128), nullable=False),
        sa.Column("input_hash", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts_snapshot", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("deadline_at", sa.DateTime(), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("output_refs_json", sa.JSON(), nullable=False),
        sa.Column("last_error_category", sa.String(length=48), nullable=True),
        sa.Column("last_error_code", sa.String(length=128), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.workflow_run_id"]),
        sa.UniqueConstraint("workflow_run_id", "parent_step_name", "activity_key", name="uq_workflow_activity_checkpoint_run_parent_key"),
    )
    op.create_index("ix_workflow_activity_checkpoint_due", "workflow_activity_checkpoints", ["status", "next_attempt_at"])
    op.create_index("ix_workflow_activity_checkpoint_parent", "workflow_activity_checkpoints", ["workflow_run_id", "parent_step_name"])


def downgrade() -> None:
    if "workflow_activity_checkpoints" in set(sa.inspect(op.get_bind()).get_table_names()):
        op.drop_table("workflow_activity_checkpoints")
