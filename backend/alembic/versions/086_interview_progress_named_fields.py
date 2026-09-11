"""将面试过程草稿迁入正式 Interview。

Revision ID: 086_interview_progress_named_fields
Revises: 085_remove_remaining_generic_payload_models
"""
from __future__ import annotations
from alembic import op
import sqlalchemy as sa
revision = "086_interview_progress_named_fields"
down_revision = "085_remove_remaining_generic_payload_models"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column("interviews", sa.Column("guide_id", sa.String(96), nullable=True))
    op.add_column("interviews", sa.Column("progress_raw_notes", sa.Text(), nullable=True))
    op.add_column("interviews", sa.Column("progress_question_responses_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")))
    op.add_column("interviews", sa.Column("progress_version", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("interviews", sa.Column("progress_updated_by", sa.String(64), nullable=True))
    op.add_column("interviews", sa.Column("progress_created_at", sa.DateTime(), nullable=True))
    op.add_column("interviews", sa.Column("progress_updated_at", sa.DateTime(), nullable=True))
    op.create_foreign_key("fk_interviews_progress_updated_by", "interviews", "users", ["progress_updated_by"], ["user_id"])

def downgrade() -> None:
    raise RuntimeError("开发期硬删除迁移不支持降级")