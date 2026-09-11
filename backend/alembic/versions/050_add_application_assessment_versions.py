"""add application assessment versions

Revision ID: 050_add_assessment_versions
Revises: 049_remove_candidate_evidence
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "050_add_assessment_versions"
down_revision = "049_remove_candidate_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """新增申请阶段唯一正式评估结果，不迁移或删除仍供历史读取的旧表。"""
    inspector = sa.inspect(op.get_bind())
    if "application_assessment_versions" in set(inspector.get_table_names()):
        return

    op.create_table(
        "application_assessment_versions",
        sa.Column("assessment_version_id", sa.String(length=96), primary_key=True),
        sa.Column("application_id", sa.String(length=64), nullable=False),
        sa.Column("stage", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("previous_assessment_version_id", sa.String(length=96), nullable=True),
        sa.Column("resume_profile_id", sa.String(length=96), nullable=False),
        sa.Column("job_profile_id", sa.String(length=96), nullable=False),
        sa.Column("source_interview_parse_result_id", sa.String(length=96), nullable=True),
        sa.Column("source_json", sa.JSON(), nullable=False),
        sa.Column("core_result_json", sa.JSON(), nullable=False),
        sa.Column("rule_result_json", sa.JSON(), nullable=False),
        sa.Column("presentation_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["application_id"], ["applications.application_id"]),
        sa.ForeignKeyConstraint(
            ["previous_assessment_version_id"],
            ["application_assessment_versions.assessment_version_id"],
        ),
        sa.ForeignKeyConstraint(["resume_profile_id"], ["resume_profiles.resume_profile_id"]),
        sa.ForeignKeyConstraint(["job_profile_id"], ["job_requirement_profiles.job_profile_id"]),
        sa.ForeignKeyConstraint(
            ["source_interview_parse_result_id"],
            ["interview_parse_results.parse_result_id"],
        ),
        sa.UniqueConstraint(
            "application_id", "stage", "version",
            name="uq_assessment_version_application_stage_version",
        ),
    )
    op.create_index(
        "ix_application_assessment_versions_application_id",
        "application_assessment_versions", ["application_id"], unique=False,
    )
    op.create_index(
        "ix_application_assessment_versions_stage",
        "application_assessment_versions", ["stage"], unique=False,
    )
    op.create_index(
        "ix_assessment_versions_application_created",
        "application_assessment_versions", ["application_id", "created_at"], unique=False,
    )
    op.create_index(
        "ix_application_assessment_versions_resume_profile_id",
        "application_assessment_versions", ["resume_profile_id"], unique=False,
    )
    op.create_index(
        "ix_application_assessment_versions_job_profile_id",
        "application_assessment_versions", ["job_profile_id"], unique=False,
    )
    op.create_index(
        "ix_assessment_version_interview_parse",
        "application_assessment_versions", ["source_interview_parse_result_id"], unique=False,
    )


def downgrade() -> None:
    op.drop_table("application_assessment_versions")


