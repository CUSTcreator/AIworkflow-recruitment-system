"""candidate intake reason fields

Revision ID: 067_candidate_intake_reasons
Revises: 066_interview_template_archive
Create Date: 2026-08-24
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "067_candidate_intake_reasons"
down_revision = "066_interview_template_archive"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """增加简历待确认、终态失败的结构化原因字段，不改写历史业务状态。"""
    op.add_column("resume_submissions", sa.Column("review_kind", sa.String(length=32), nullable=True))
    op.add_column("resume_submissions", sa.Column("failure_kind", sa.String(length=48), nullable=True))
    op.create_index("ix_resume_submissions_review_kind", "resume_submissions", ["review_kind"])
    op.create_index("ix_resume_submissions_failure_kind", "resume_submissions", ["failure_kind"])


def downgrade() -> None:
    """仅回退新增列和索引，不改写已有简历任务。"""
    op.drop_index("ix_resume_submissions_failure_kind", table_name="resume_submissions")
    op.drop_index("ix_resume_submissions_review_kind", table_name="resume_submissions")
    op.drop_column("resume_submissions", "failure_kind")
    op.drop_column("resume_submissions", "review_kind")