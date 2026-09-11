"""Make ResumeSubmission the canonical resume processing record.

Revision ID: 045_submission_versions
Revises: 044_application_sources
"""

from alembic import op
import sqlalchemy as sa


revision = "045_submission_versions"
down_revision = "044_application_sources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("resume_submissions", sa.Column("supersedes_submission_id", sa.String(64), nullable=True))
    op.add_column("resume_submissions", sa.Column("output_resume_profile_id", sa.String(96), nullable=True))
    op.add_column("resume_submissions", sa.Column("parsed_text", sa.Text(), nullable=True))
    op.add_column("resume_submissions", sa.Column("parse_metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    op.create_index("ix_resume_submissions_supersedes_submission_id", "resume_submissions", ["supersedes_submission_id"])
    op.create_index("ix_resume_submissions_output_resume_profile_id", "resume_submissions", ["output_resume_profile_id"])
    op.create_foreign_key("fk_resume_submissions_supersedes", "resume_submissions", "resume_submissions", ["supersedes_submission_id"], ["resume_submission_id"])
    op.create_foreign_key("fk_resume_submissions_output_profile", "resume_submissions", "resume_profiles", ["output_resume_profile_id"], ["resume_profile_id"])

    # 应用采用的是哪次简历处理，而不是模糊的“来源”字段。
    op.drop_constraint("fk_applications_source_resume_submission", "applications", type_="foreignkey")
    op.alter_column("applications", "source_resume_submission_id", new_column_name="adopted_resume_submission_id")
    op.drop_index("ix_applications_source_resume_submission_id", table_name="applications")
    op.create_index("ix_applications_adopted_resume_submission_id", "applications", ["adopted_resume_submission_id"])
    op.create_foreign_key("fk_applications_adopted_resume_submission", "applications", "resume_submissions", ["adopted_resume_submission_id"], ["resume_submission_id"])

    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    # 历史解析结果原先放在 SourceDocument：回填到每次 Submission，避免同一 PDF
    # 被重新解析时覆盖前一次处理事实。
    bind.execute(sa.text("""
        UPDATE resume_submissions submission
        SET parsed_text = document.parsed_text,
            parse_metadata = COALESCE(document.parse_metadata::json, '{}'::json)
        FROM source_documents document
        WHERE submission.source_document_id = document.source_document_id
          AND submission.parsed_text IS NULL
    """))
    bind.execute(sa.text("""
        UPDATE resume_submissions submission
        SET supersedes_submission_id = submission.rebuild_from_submission_id
        WHERE submission.supersedes_submission_id IS NULL
          AND submission.rebuild_from_submission_id IS NOT NULL
    """))
    bind.execute(sa.text("""
        UPDATE resume_submissions submission
        SET output_resume_profile_id = candidate.current_resume_profile_id
        FROM candidates candidate
        WHERE submission.candidate_id = candidate.candidate_id
          AND submission.output_resume_profile_id IS NULL
          AND submission.resume_submission_id = candidate.current_resume_submission_id
    """))


def downgrade() -> None:
    op.drop_constraint("fk_applications_adopted_resume_submission", "applications", type_="foreignkey")
    op.drop_index("ix_applications_adopted_resume_submission_id", table_name="applications")
    op.alter_column("applications", "adopted_resume_submission_id", new_column_name="source_resume_submission_id")
    op.create_index("ix_applications_source_resume_submission_id", "applications", ["source_resume_submission_id"])
    op.create_foreign_key("fk_applications_source_resume_submission", "applications", "resume_submissions", ["source_resume_submission_id"], ["resume_submission_id"])
    op.drop_constraint("fk_resume_submissions_output_profile", "resume_submissions", type_="foreignkey")
    op.drop_constraint("fk_resume_submissions_supersedes", "resume_submissions", type_="foreignkey")
    op.drop_index("ix_resume_submissions_output_resume_profile_id", table_name="resume_submissions")
    op.drop_index("ix_resume_submissions_supersedes_submission_id", table_name="resume_submissions")
    op.drop_column("resume_submissions", "parse_metadata")
    op.drop_column("resume_submissions", "parsed_text")
    op.drop_column("resume_submissions", "output_resume_profile_id")
    op.drop_column("resume_submissions", "supersedes_submission_id")