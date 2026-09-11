"""add named resume structure metadata

Revision ID: 075_resume_structure_metadata
Revises: 074_resume_intake_contracts
"""

from alembic import op
import sqlalchemy as sa


revision = "075_resume_structure_metadata"
down_revision = "074_resume_intake_contracts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """为已通过结构化、尚未发布画像的 Submission 保存正式基础信息。"""
    with op.batch_alter_table("resume_submissions") as batch:
        batch.add_column(
            sa.Column(
                "structure_metadata_json",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("resume_submissions") as batch:
        batch.drop_column("structure_metadata_json")
