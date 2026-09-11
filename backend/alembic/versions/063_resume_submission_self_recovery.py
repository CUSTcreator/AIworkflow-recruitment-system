"""Add explicit self-service recovery inputs to resume submissions.

Revision ID: 063_resume_submission_self_recovery
Revises: 062_normalize_job_draft_lists
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "063_resume_submission_self_recovery"
down_revision = "062_normalize_job_draft_lists"
branch_labels = None
depends_on = None


_MODE_CHECK = "intake_mode IN ('initial', 'reparse', 'replacement', 'manual_correction')"
_PARSE_STRATEGY_CHECK = "parse_strategy IN ('reuse_verified_parse', 'force_fresh_parse')"


def upgrade() -> None:
    op.add_column(
        "resume_submissions",
        sa.Column(
            "parse_strategy",
            sa.String(length=32),
            nullable=False,
            server_default="reuse_verified_parse",
        ),
    )
    op.add_column(
        "resume_submissions",
        sa.Column("manual_correction_ref", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "resume_submissions",
        sa.Column("manual_correction_sha256", sa.String(length=128), nullable=True),
    )
    op.drop_constraint("ck_resume_submissions_intake_mode", "resume_submissions", type_="check")
    op.create_check_constraint("ck_resume_submissions_intake_mode", "resume_submissions", _MODE_CHECK)
    op.create_check_constraint("ck_resume_submissions_parse_strategy", "resume_submissions", _PARSE_STRATEGY_CHECK)
    op.alter_column("resume_submissions", "parse_strategy", server_default=None)


def downgrade() -> None:
    op.drop_constraint("ck_resume_submissions_parse_strategy", "resume_submissions", type_="check")
    op.drop_constraint("ck_resume_submissions_intake_mode", "resume_submissions", type_="check")
    op.create_check_constraint(
        "ck_resume_submissions_intake_mode",
        "resume_submissions",
        "intake_mode IN ('initial', 'reparse', 'replacement')",
    )
    op.drop_column("resume_submissions", "manual_correction_sha256")
    op.drop_column("resume_submissions", "manual_correction_ref")
    op.drop_column("resume_submissions", "parse_strategy")
