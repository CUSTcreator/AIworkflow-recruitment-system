"""增加 Activity 降级/阻塞业务结论。

Revision ID: 087_activity_outcome_contract
Revises: 086_interview_progress_named_fields
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "087_activity_outcome_contract"
down_revision = "086_interview_progress_named_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 历史成功行保持 NULL，由运行时按 completed 兼容读取；不回填大表，也不改写历史产物。
    op.add_column("workflow_activity_checkpoints", sa.Column("outcome_kind", sa.String(24), nullable=True))
    op.add_column("workflow_activity_checkpoints", sa.Column("resolution_code", sa.String(128), nullable=True))
    op.add_column(
        "workflow_activity_checkpoints",
        sa.Column("quality_summary_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.create_index(
        "ix_workflow_activity_checkpoint_outcome_kind",
        "workflow_activity_checkpoints",
        ["outcome_kind"],
    )


def downgrade() -> None:
    raise RuntimeError("开发期运行时合同迁移不支持降级")