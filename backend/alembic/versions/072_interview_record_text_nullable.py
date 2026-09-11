"""allow empty interview records without synthetic text

Revision ID: 072_interview_record_nullable
Revises: 071_resume_profile_revision
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "072_interview_record_nullable"
down_revision = "071_resume_profile_revision"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """将未作答题目的原文设为 NULL；NULL 表示无证据，不表示数据损坏。"""
    columns = {item["name"]: item for item in sa.inspect(op.get_bind()).get_columns("interview_records")}
    if "raw_text" not in columns or columns["raw_text"].get("nullable"):
        return
    with op.batch_alter_table("interview_records") as batch:
        batch.alter_column("raw_text", existing_type=sa.Text(), nullable=True)


def downgrade() -> None:
    """历史空值先规范为空字符串，才恢复旧版非空约束。"""
    columns = {item["name"]: item for item in sa.inspect(op.get_bind()).get_columns("interview_records")}
    if "raw_text" not in columns or not columns["raw_text"].get("nullable"):
        return
    op.execute(sa.text("UPDATE interview_records SET raw_text = '' WHERE raw_text IS NULL"))
    with op.batch_alter_table("interview_records") as batch:
        batch.alter_column("raw_text", existing_type=sa.Text(), nullable=False)