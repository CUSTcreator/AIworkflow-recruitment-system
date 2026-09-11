"""add workflow step checkpoints

Revision ID: 053_workflow_step_checkpoints
Revises: 052_post_interview_contracts
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "053_workflow_step_checkpoints"
down_revision = "052_post_interview_contracts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """新增步骤级恢复账本；不迁移或篡改运行中的旧 WorkflowRun。"""
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "workflow_step_checkpoints" not in tables:
        op.create_table(
            "workflow_step_checkpoints",
            sa.Column("checkpoint_id", sa.String(length=64), primary_key=True),
            sa.Column("workflow_run_id", sa.String(length=64), nullable=False),
            sa.Column("step_name", sa.String(length=96), nullable=False),
            sa.Column("step_order", sa.Integer(), nullable=False),
            sa.Column("definition_version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
            sa.Column("input_hash", sa.String(length=128), nullable=False),
            sa.Column("idempotency_key", sa.String(length=128), nullable=False),
            sa.Column("external_job_id", sa.String(length=256), nullable=True),
            sa.Column("output_refs_json", sa.JSON(), nullable=False),
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("poll_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
            sa.Column("deadline_at", sa.DateTime(), nullable=True),
            sa.Column("last_error_code", sa.String(length=128), nullable=True),
            sa.Column("last_error_message", sa.Text(), nullable=True),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.workflow_run_id"]),
            sa.UniqueConstraint("workflow_run_id", "step_name", name="uq_workflow_step_checkpoint_run_step"),
        )
        op.create_index("ix_workflow_step_checkpoint_due", "workflow_step_checkpoints", ["status", "next_attempt_at"])
        op.create_index("ix_workflow_step_checkpoint_run_order", "workflow_step_checkpoints", ["workflow_run_id", "step_order"])
        op.create_index("ix_workflow_step_checkpoint_external_job", "workflow_step_checkpoints", ["external_job_id"])
    if "workflow_runs" in tables:
        columns = {column["name"] for column in inspector.get_columns("workflow_runs")}
        if "definition_version" not in columns:
            with op.batch_alter_table("workflow_runs") as batch:
                batch.add_column(sa.Column("definition_version", sa.Integer(), nullable=False, server_default="1"))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "workflow_step_checkpoints" in tables:
        op.drop_table("workflow_step_checkpoints")
    if "workflow_runs" in tables:
        columns = {column["name"] for column in inspector.get_columns("workflow_runs")}
        if "definition_version" in columns:
            with op.batch_alter_table("workflow_runs") as batch:
                batch.drop_column("definition_version")