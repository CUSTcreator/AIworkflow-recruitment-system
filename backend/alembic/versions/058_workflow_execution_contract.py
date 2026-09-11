"""add workflow execution contract checkpoint fields

Revision ID: 058_workflow_execution_contract
Revises: 057_interview_q_links
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "058_workflow_execution_contract"
down_revision = "057_interview_q_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """为已有检查点补充稳定的错误类别和重试策略快照，不改写历史业务产物。"""
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if "workflow_step_checkpoints" not in set(inspector.get_table_names()):
        return
    columns = {column["name"] for column in inspector.get_columns("workflow_step_checkpoints")}
    indexes = {index["name"] for index in inspector.get_indexes("workflow_step_checkpoints")}
    with op.batch_alter_table("workflow_step_checkpoints") as batch:
        if "max_attempts_snapshot" not in columns:
            batch.add_column(
                # 0 表示迁移前没有可还原的策略快照；运行时将回退到定义中的策略。
                sa.Column("max_attempts_snapshot", sa.Integer(), nullable=False, server_default="0")
            )
        if "last_error_category" not in columns:
            batch.add_column(sa.Column("last_error_category", sa.String(length=48), nullable=True))
        if "ix_workflow_step_checkpoint_status_updated" not in indexes:
            batch.create_index("ix_workflow_step_checkpoint_status_updated", ["status", "updated_at"])
        if "ix_workflow_step_checkpoint_error_category" not in indexes:
            batch.create_index("ix_workflow_step_checkpoint_error_category", ["last_error_category"])


def downgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if "workflow_step_checkpoints" not in set(inspector.get_table_names()):
        return
    columns = {column["name"] for column in inspector.get_columns("workflow_step_checkpoints")}
    indexes = {index["name"] for index in inspector.get_indexes("workflow_step_checkpoints")}
    with op.batch_alter_table("workflow_step_checkpoints") as batch:
        if "ix_workflow_step_checkpoint_error_category" in indexes:
            batch.drop_index("ix_workflow_step_checkpoint_error_category")
        if "ix_workflow_step_checkpoint_status_updated" in indexes:
            batch.drop_index("ix_workflow_step_checkpoint_status_updated")
        if "last_error_category" in columns:
            batch.drop_column("last_error_category")
        if "max_attempts_snapshot" in columns:
            batch.drop_column("max_attempts_snapshot")