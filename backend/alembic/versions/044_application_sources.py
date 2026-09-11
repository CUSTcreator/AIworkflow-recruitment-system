"""Backfill application source links from the candidate aggregate.

Revision ID: 044_application_sources
Revises: 043_candidate_pointers
"""

from alembic import op
import sqlalchemy as sa


revision = "044_application_sources"
down_revision = "043_candidate_pointers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    # 历史 Application 没有记录来源简历时，以同一 Candidate 已回填的当前
    # 简历和画像为准。新写入路径会在创建/重建时直接写入这两个字段。
    bind.execute(sa.text("""
        UPDATE applications application
        SET source_resume_submission_id = candidate.current_resume_submission_id,
            source_resume_profile_id = COALESCE(
                application.source_resume_profile_id,
                candidate.current_resume_profile_id
            )
        FROM candidates candidate
        WHERE application.candidate_id = candidate.candidate_id
          AND application.source_resume_submission_id IS NULL
          AND candidate.current_resume_submission_id IS NOT NULL
    """))


def downgrade() -> None:
    pass