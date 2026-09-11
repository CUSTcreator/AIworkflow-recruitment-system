"""Normalize Candidate, Application and ResumeSubmission fields that were stored in payload JSON.

Revision ID: 042_candidate_aggregate_fields
Revises: 041_workflow_request_correlation
"""

from alembic import op
import sqlalchemy as sa


revision = "042_candidate_aggregate_fields"
down_revision = "041_workflow_request_correlation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("candidates", sa.Column("current_resume_submission_id", sa.String(64), nullable=True))
    op.add_column("candidates", sa.Column("current_resume_profile_id", sa.String(96), nullable=True))
    op.add_column("candidates", sa.Column("resume_intake_status", sa.String(32), nullable=False, server_default="processing"))
    op.add_column("candidates", sa.Column("resume_rebuild_status", sa.String(32), nullable=False, server_default="idle"))
    op.add_column("candidates", sa.Column("resume_rebuild_submission_id", sa.String(64), nullable=True))
    op.add_column("candidates", sa.Column("resume_rebuild_mode", sa.String(32), nullable=True))
    op.add_column("candidates", sa.Column("resume_rebuild_message", sa.Text(), nullable=True))
    op.create_index("ix_candidates_current_resume_submission_id", "candidates", ["current_resume_submission_id"])
    op.create_index("ix_candidates_current_resume_profile_id", "candidates", ["current_resume_profile_id"])
    op.create_index("ix_candidates_resume_intake_status", "candidates", ["resume_intake_status"])
    op.create_index("ix_candidates_resume_rebuild_status", "candidates", ["resume_rebuild_status"])
    op.create_index("ix_candidates_resume_rebuild_submission_id", "candidates", ["resume_rebuild_submission_id"])
    op.create_foreign_key("fk_candidates_current_resume_submission", "candidates", "resume_submissions", ["current_resume_submission_id"], ["resume_submission_id"])
    op.create_foreign_key("fk_candidates_current_resume_profile", "candidates", "resume_profiles", ["current_resume_profile_id"], ["resume_profile_id"])

    op.create_table(
        "candidate_profiles",
        sa.Column("candidate_id", sa.String(64), nullable=False),
        sa.Column("current_title", sa.String(255), nullable=False, server_default=""),
        sa.Column("years_of_experience", sa.String(64), nullable=False, server_default=""),
        sa.Column("education", sa.String(255), nullable=False, server_default=""),
        sa.Column("age", sa.Integer(), nullable=True),
        sa.Column("school", sa.String(255), nullable=False, server_default=""),
        sa.Column("major", sa.String(255), nullable=False, server_default=""),
        sa.Column("phone", sa.String(64), nullable=False, server_default=""),
        sa.Column("email", sa.String(255), nullable=False, server_default=""),
        sa.Column("highest_degree", sa.String(64), nullable=False, server_default=""),
        sa.Column("graduation_year", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["candidate_id"], ["candidates.candidate_id"]),
        sa.PrimaryKeyConstraint("candidate_id"),
    )

    op.add_column("resume_submissions", sa.Column("intake_mode", sa.String(32), nullable=False, server_default="initial"))
    op.add_column("resume_submissions", sa.Column("rebuild_from_submission_id", sa.String(64), nullable=True))
    op.create_index("ix_resume_submissions_intake_mode", "resume_submissions", ["intake_mode"])
    op.create_index("ix_resume_submissions_rebuild_from_submission_id", "resume_submissions", ["rebuild_from_submission_id"])

    op.add_column("applications", sa.Column("source_resume_submission_id", sa.String(64), nullable=True))
    op.add_column("applications", sa.Column("resume_rebuild_status", sa.String(32), nullable=False, server_default="idle"))
    op.add_column("applications", sa.Column("resume_rebuild_submission_id", sa.String(64), nullable=True))
    op.add_column("applications", sa.Column("resume_rebuild_message", sa.Text(), nullable=True))
    op.create_index("ix_applications_source_resume_submission_id", "applications", ["source_resume_submission_id"])
    op.create_index("ix_applications_resume_rebuild_status", "applications", ["resume_rebuild_status"])
    op.create_index("ix_applications_resume_rebuild_submission_id", "applications", ["resume_rebuild_submission_id"])
    op.create_foreign_key("fk_applications_source_resume_submission", "applications", "resume_submissions", ["source_resume_submission_id"], ["resume_submission_id"])

    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    bind.execute(sa.text("""
        INSERT INTO candidate_profiles (
            candidate_id, current_title, years_of_experience, education, age,
            school, major, phone, email, highest_degree, graduation_year, updated_at
        )
        SELECT candidate_id,
            COALESCE(payload->>'currentTitle', ''),
            COALESCE(payload->>'yearsOfExperience', ''),
            COALESCE(payload->>'education', ''),
            CASE WHEN COALESCE(payload->>'age', '') ~ '^[0-9]+$' THEN (payload->>'age')::integer END,
            COALESCE(payload->>'school', ''), COALESCE(payload->>'major', ''),
            COALESCE(payload->>'phone', ''), COALESCE(payload->>'email', ''),
            COALESCE(payload->>'highestDegree', ''),
            CASE WHEN COALESCE(payload->>'graduationYear', '') ~ '^[0-9]+$' THEN (payload->>'graduationYear')::integer END,
            updated_at
        FROM candidates
        ON CONFLICT (candidate_id) DO NOTHING
    """))
    bind.execute(sa.text("""
        UPDATE candidates SET
            current_resume_submission_id = NULLIF(payload->>'currentResumeSubmissionId', ''),
            current_resume_profile_id = NULLIF(payload->>'resumeProfileId', ''),
            resume_intake_status = COALESCE(NULLIF(payload->>'resumeIntakeStatus', ''), resume_intake_status),
            resume_rebuild_status = COALESCE(NULLIF(payload->>'resumeRebuildStatus', ''), resume_rebuild_status),
            resume_rebuild_submission_id = NULLIF(payload->>'resumeRebuildSubmissionId', ''),
            resume_rebuild_mode = NULLIF(payload->>'resumeRebuildMode', ''),
            resume_rebuild_message = NULLIF(payload->>'resumeRebuildMessage', '')
    """))
    bind.execute(sa.text("""
        UPDATE resume_submissions SET
            intake_mode = COALESCE(NULLIF(payload->'candidateRebuild'->>'mode', ''), intake_mode),
            rebuild_from_submission_id = NULLIF(payload->'candidateRebuild'->>'sourceSubmissionId', '')
    """))
    bind.execute(sa.text("""
        UPDATE applications SET
            resume_rebuild_status = COALESCE(NULLIF(payload->>'resumeRebuildStatus', ''), resume_rebuild_status),
            resume_rebuild_submission_id = NULLIF(payload->>'resumeRebuildSubmissionId', ''),
            resume_rebuild_message = NULLIF(payload->>'resumeRebuildMessage', '')
    """))


def downgrade() -> None:
    op.drop_constraint("fk_applications_source_resume_submission", "applications", type_="foreignkey")
    op.drop_index("ix_applications_resume_rebuild_submission_id", table_name="applications")
    op.drop_index("ix_applications_resume_rebuild_status", table_name="applications")
    op.drop_index("ix_applications_source_resume_submission_id", table_name="applications")
    op.drop_column("applications", "resume_rebuild_message")
    op.drop_column("applications", "resume_rebuild_submission_id")
    op.drop_column("applications", "resume_rebuild_status")
    op.drop_column("applications", "source_resume_submission_id")
    op.drop_index("ix_resume_submissions_rebuild_from_submission_id", table_name="resume_submissions")
    op.drop_index("ix_resume_submissions_intake_mode", table_name="resume_submissions")
    op.drop_column("resume_submissions", "rebuild_from_submission_id")
    op.drop_column("resume_submissions", "intake_mode")
    op.drop_table("candidate_profiles")
    op.drop_constraint("fk_candidates_current_resume_profile", "candidates", type_="foreignkey")
    op.drop_constraint("fk_candidates_current_resume_submission", "candidates", type_="foreignkey")
    op.drop_index("ix_candidates_resume_rebuild_submission_id", table_name="candidates")
    op.drop_index("ix_candidates_resume_rebuild_status", table_name="candidates")
    op.drop_index("ix_candidates_resume_intake_status", table_name="candidates")
    op.drop_index("ix_candidates_current_resume_profile_id", table_name="candidates")
    op.drop_index("ix_candidates_current_resume_submission_id", table_name="candidates")
    op.drop_column("candidates", "resume_rebuild_message")
    op.drop_column("candidates", "resume_rebuild_mode")
    op.drop_column("candidates", "resume_rebuild_submission_id")
    op.drop_column("candidates", "resume_rebuild_status")
    op.drop_column("candidates", "resume_intake_status")
    op.drop_column("candidates", "current_resume_profile_id")
    op.drop_column("candidates", "current_resume_submission_id")

