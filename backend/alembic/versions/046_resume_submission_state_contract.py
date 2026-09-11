"""收紧 ResumeSubmission 状态与处理模式契约。

Revision ID: 046_resume_submission_state_contract
Revises: 045_submission_versions
"""

from alembic import op


revision = "046_resume_submission_states"
down_revision = "045_submission_versions"
branch_labels = None
depends_on = None


_STATUS_CHECK = "status IN ('queued', 'parsing', 'extracting', 'review_required', 'failed', 'completed')"
_MODE_CHECK = "intake_mode IN ('initial', 'reparse', 'replacement')"


def upgrade() -> None:
    # 旧状态仅是“需要人工处理”的不同原因；原因转存到结构化元数据，
    # 状态本身收敛为 review_required，便于数据库和接口使用一套契约。
    op.execute("""
        UPDATE resume_submissions
        SET parse_metadata = COALESCE(parse_metadata::jsonb, '{}'::jsonb)
            || jsonb_build_object(
                'review_kind',
                CASE status
                    WHEN 'structure_review_required' THEN 'structure'
                    WHEN 'duplicate_review_required' THEN 'duplicate'
                    WHEN 'duplicate_blocked' THEN 'duplicate_blocked'
                    ELSE COALESCE(parse_metadata::jsonb ->> 'review_kind', 'manual')
                END
            )
        WHERE status IN (
            'structure_review_required',
            'duplicate_review_required',
            'duplicate_blocked'
        )
    """)
    op.execute("""
        UPDATE resume_submissions
        SET status = CASE status
            WHEN 'structure_review_required' THEN 'review_required'
            WHEN 'duplicate_review_required' THEN 'review_required'
            WHEN 'duplicate_blocked' THEN 'review_required'
            WHEN 'parsed' THEN 'extracting'
            WHEN 'discarded' THEN 'completed'
            WHEN 'pending' THEN 'queued'
            WHEN 'running' THEN 'parsing'
            ELSE status
        END
    """)
    op.create_check_constraint(
        "ck_resume_submissions_status", "resume_submissions", _STATUS_CHECK
    )
    op.create_check_constraint(
        "ck_resume_submissions_intake_mode", "resume_submissions", _MODE_CHECK
    )


def downgrade() -> None:
    op.drop_constraint("ck_resume_submissions_intake_mode", "resume_submissions", type_="check")
    op.drop_constraint("ck_resume_submissions_status", "resume_submissions", type_="check")