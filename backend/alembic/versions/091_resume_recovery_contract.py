"""Persist user-actionable recovery information for resume submissions.

Revision ID: 091_resume_recovery_contract
Revises: 090_unify_interview_assertions
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "091_resume_recovery_contract"
down_revision = "090_unify_interview_assertions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("resume_submissions", sa.Column("recovery_code", sa.String(length=64), nullable=True))
    op.add_column(
        "resume_submissions",
        sa.Column("recovery_context_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.create_index("ix_resume_submissions_recovery_code", "resume_submissions", ["recovery_code"])
    op.alter_column("resume_submissions", "recovery_context_json", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_resume_submissions_recovery_code", table_name="resume_submissions")
    op.drop_column("resume_submissions", "recovery_context_json")
    op.drop_column("resume_submissions", "recovery_code")
