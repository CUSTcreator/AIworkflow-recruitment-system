"""generalize workflow artifact ownership

Revision ID: 054_workflow_artifact_v2
Revises: 053_workflow_step_checkpoints
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "054_workflow_artifact_v2"
down_revision = "053_workflow_step_checkpoints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """允许阶段产物归属非 Application 的业务对象。"""
    inspector = sa.inspect(op.get_bind())
    if "workflow_artifacts" not in set(inspector.get_table_names()):
        return
    columns = {column["name"] for column in inspector.get_columns("workflow_artifacts")}
    with op.batch_alter_table("workflow_artifacts") as batch:
        if "subject_type" not in columns:
            batch.add_column(sa.Column("subject_type", sa.String(length=32), nullable=True))
        if "subject_id" not in columns:
            batch.add_column(sa.Column("subject_id", sa.String(length=96), nullable=True))
        batch.alter_column("application_id", existing_type=sa.String(length=64), nullable=True)
    op.create_index("ix_workflow_artifacts_subject", "workflow_artifacts", ["subject_type", "subject_id"], unique=False)


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "workflow_artifacts" not in set(inspector.get_table_names()):
        return
    op.drop_index("ix_workflow_artifacts_subject", table_name="workflow_artifacts")
    with op.batch_alter_table("workflow_artifacts") as batch:
        batch.drop_column("subject_id")
        batch.drop_column("subject_type")
        # 历史 Artifact 可能没有 Application，降级时不能安全恢复 NOT NULL 约束。