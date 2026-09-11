"""Backfill explicit Candidate current resume pointers from historical child records.

Revision ID: 043_candidate_pointers
Revises: 042_candidate_aggregate_fields
"""

from alembic import op
import sqlalchemy as sa


revision = "043_candidate_pointers"
down_revision = "042_candidate_aggregate_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    # 相关子查询直接关联 UPDATE 目标表；PostgreSQL 不允许这里通过
    # FROM LATERAL 引用候选人别名。
    bind.execute(sa.text("""
        UPDATE candidates candidate
        SET current_resume_submission_id = (
            SELECT submission.resume_submission_id
            FROM resume_submissions submission
            WHERE submission.candidate_id = candidate.candidate_id
              AND COALESCE(submission.payload->>'intakeVisibility', '') <> 'superseded'
            ORDER BY submission.updated_at DESC, submission.resume_submission_id DESC
            LIMIT 1
        )
        WHERE candidate.current_resume_submission_id IS NULL
          AND EXISTS (
              SELECT 1
              FROM resume_submissions submission
              WHERE submission.candidate_id = candidate.candidate_id
                AND COALESCE(submission.payload->>'intakeVisibility', '') <> 'superseded'
          )
    """))
    bind.execute(sa.text("""
        UPDATE candidates candidate
        SET current_resume_profile_id = (
            SELECT profile.resume_profile_id
            FROM resume_profiles profile
            WHERE profile.candidate_id = candidate.candidate_id
            ORDER BY profile.version DESC, profile.created_at DESC
            LIMIT 1
        )
        WHERE candidate.current_resume_profile_id IS NULL
          AND EXISTS (
              SELECT 1
              FROM resume_profiles profile
              WHERE profile.candidate_id = candidate.candidate_id
          )
    """))


def downgrade() -> None:
    pass