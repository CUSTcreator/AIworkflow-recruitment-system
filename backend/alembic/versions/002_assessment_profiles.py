"""versioned assessment domain objects

Revision ID: 002_assessment_profiles
Revises: 001_initial_schema
"""

from alembic import op
import sqlalchemy as sa

from backend.app.models.entities import JsonType


revision = "002_assessment_profiles"
down_revision = "001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    required_tables = {
        "resume_profiles",
        "job_requirement_profiles",
        "university_ranking_entries",
        "interview_parse_results",
        "candidate_evidence_snapshots",
        "candidate_capability_profiles",
    }
    if required_tables <= set(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.create_table(
        "resume_profiles",
        sa.Column("resume_profile_id", sa.String(96), primary_key=True),
        sa.Column("candidate_id", sa.String(64), sa.ForeignKey("candidates.candidate_id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("source_sha256", sa.String(128), nullable=False),
        sa.Column("payload", JsonType, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_resume_profiles_candidate_id", "resume_profiles", ["candidate_id"])
    op.create_index("ix_resume_profiles_source_sha256", "resume_profiles", ["source_sha256"])
    op.create_table(
        "job_requirement_profiles",
        sa.Column("job_profile_id", sa.String(96), primary_key=True),
        sa.Column("job_id", sa.String(64), sa.ForeignKey("jobs.job_id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("source_sha256", sa.String(128), nullable=False),
        sa.Column("payload", JsonType, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_job_requirement_profiles_job_id", "job_requirement_profiles", ["job_id"])
    op.create_index("ix_job_requirement_profiles_source_sha256", "job_requirement_profiles", ["source_sha256"])
    op.create_table(
        "university_ranking_entries",
        sa.Column("entry_id", sa.String(96), primary_key=True),
        sa.Column("dataset_version", sa.String(64), nullable=False),
        sa.Column("ranking_source", sa.String(128), nullable=False),
        sa.Column("ranking_year", sa.Integer(), nullable=False),
        sa.Column("canonical_name", sa.String(128), nullable=False),
        sa.Column("aliases", JsonType, nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("school_score", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("dataset_version", "rank", name="uq_university_ranking_dataset_rank"),
        sa.UniqueConstraint("dataset_version", "canonical_name", name="uq_university_ranking_dataset_name"),
    )
    op.create_index("ix_university_ranking_entries_dataset_version", "university_ranking_entries", ["dataset_version"])
    op.create_index("ix_university_ranking_entries_canonical_name", "university_ranking_entries", ["canonical_name"])
    op.create_table(
        "interview_parse_results",
        sa.Column("parse_result_id", sa.String(96), primary_key=True),
        sa.Column("application_id", sa.String(64), sa.ForeignKey("applications.application_id"), nullable=False),
        sa.Column("interview_id", sa.String(64), sa.ForeignKey("interviews.interview_id"), nullable=True),
        sa.Column("stage", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("payload", JsonType, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("application_id", "stage", "version", name="uq_interview_parse_application_stage_version"),
    )
    op.create_index("ix_interview_parse_results_application_id", "interview_parse_results", ["application_id"])
    op.create_index("ix_interview_parse_results_stage", "interview_parse_results", ["stage"])
    op.create_table(
        "candidate_evidence_snapshots",
        sa.Column("evidence_snapshot_id", sa.String(96), primary_key=True),
        sa.Column("application_id", sa.String(64), sa.ForeignKey("applications.application_id"), nullable=False),
        sa.Column("stage", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("previous_snapshot_id", sa.String(96), sa.ForeignKey("candidate_evidence_snapshots.evidence_snapshot_id"), nullable=True),
        sa.Column("resume_profile_id", sa.String(96), sa.ForeignKey("resume_profiles.resume_profile_id"), nullable=False),
        sa.Column("payload", JsonType, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("application_id", "stage", "version", name="uq_evidence_snapshot_application_stage_version"),
    )
    op.create_index("ix_candidate_evidence_snapshots_application_id", "candidate_evidence_snapshots", ["application_id"])
    op.create_index("ix_candidate_evidence_snapshots_stage", "candidate_evidence_snapshots", ["stage"])
    op.create_table(
        "candidate_capability_profiles",
        sa.Column("profile_id", sa.String(96), primary_key=True),
        sa.Column("application_id", sa.String(64), sa.ForeignKey("applications.application_id"), nullable=False),
        sa.Column("stage", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("previous_profile_id", sa.String(96), sa.ForeignKey("candidate_capability_profiles.profile_id"), nullable=True),
        sa.Column("evidence_snapshot_id", sa.String(96), sa.ForeignKey("candidate_evidence_snapshots.evidence_snapshot_id"), nullable=False),
        sa.Column("resume_profile_id", sa.String(96), sa.ForeignKey("resume_profiles.resume_profile_id"), nullable=False),
        sa.Column("job_profile_id", sa.String(96), sa.ForeignKey("job_requirement_profiles.job_profile_id"), nullable=False),
        sa.Column("payload", JsonType, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("application_id", "stage", "version", name="uq_capability_profile_application_stage_version"),
    )
    op.create_index("ix_candidate_capability_profiles_application_id", "candidate_capability_profiles", ["application_id"])
    op.create_index("ix_candidate_capability_profiles_stage", "candidate_capability_profiles", ["stage"])


def downgrade() -> None:
    op.drop_table("candidate_capability_profiles")
    op.drop_table("candidate_evidence_snapshots")
    op.drop_table("interview_parse_results")
    op.drop_table("job_requirement_profiles")
    op.drop_table("university_ranking_entries")
    op.drop_table("resume_profiles")
