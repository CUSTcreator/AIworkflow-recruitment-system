"""freeze asynchronous workflow poll policy

Revision ID: 059_workflow_step_poll_policy
Revises: 058_workflow_execution_contract
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "059_workflow_step_poll_policy"
down_revision = "058_workflow_execution_contract"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """保存轮询上限快照；迁移前检查点仍以当前定义中的策略作为兼容回退。"""
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if "workflow_step_checkpoints" not in set(inspector.get_table_names()):
        return
    columns = {column["name"] for column in inspector.get_columns("workflow_step_checkpoints")}
    if "max_poll_attempts_snapshot" not in columns:
        with op.batch_alter_table("workflow_step_checkpoints") as batch:
            batch.add_column(
                sa.Column(
                    "max_poll_attempts_snapshot",
                    sa.Integer(),
                    nullable=False,
                    server_default="0",
                )
            )


def downgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if "workflow_step_checkpoints" not in set(inspector.get_table_names()):
        return
    columns = {column["name"] for column in inspector.get_columns("workflow_step_checkpoints")}
    if "max_poll_attempts_snapshot" in columns:
        with op.batch_alter_table("workflow_step_checkpoints") as batch:
            batch.drop_column("max_poll_attempts_snapshot")