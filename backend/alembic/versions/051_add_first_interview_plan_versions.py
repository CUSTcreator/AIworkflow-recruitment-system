"""add first interview plan versions

Revision ID: 051_first_interview_plans
Revises: 050_add_assessment_versions
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "051_first_interview_plans"
down_revision = "050_add_assessment_versions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """新增一面题单规划版本；正式题单仍在确认动作后写入旧的执行实体。"""
    inspector = sa.inspect(op.get_bind())
    if "first_interview_plan_versions" in set(inspector.get_table_names()):
        return

    op.create_table(
        "first_interview_plan_versions",
        sa.Column("plan_version_id", sa.String(length=96), primary_key=True),
        sa.Column("application_id", sa.String(length=64), nullable=False),
        sa.Column("source_assessment_version_id", sa.String(length=96), nullable=False),
        sa.Column("previous_plan_version_id", sa.String(length=96), nullable=True),
        sa.Column("template_version_id", sa.String(length=96), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("source_json", sa.JSON(), nullable=False),
        sa.Column("core_result_json", sa.JSON(), nullable=False),
        sa.Column("rule_result_json", sa.JSON(), nullable=False),
        sa.Column("presentation_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["application_id"], ["applications.application_id"]),
        sa.ForeignKeyConstraint(
            ["source_assessment_version_id"],
            ["application_assessment_versions.assessment_version_id"],
        ),
        sa.ForeignKeyConstraint(
            ["previous_plan_version_id"],
            ["first_interview_plan_versions.plan_version_id"],
        ),
        sa.UniqueConstraint(
            "application_id", "version",
            name="uq_first_interview_plan_application_version",
        ),
    )
    op.create_index(
        "ix_first_interview_plan_versions_application_id",
        "first_interview_plan_versions", ["application_id"], unique=False,
    )
    op.create_index(
        "ix_first_interview_plan_versions_source_assessment_version_id",
        "first_interview_plan_versions", ["source_assessment_version_id"], unique=False,
    )
    op.create_index(
        "ix_first_interview_plan_application_created",
        "first_interview_plan_versions", ["application_id", "created_at"], unique=False,
    )
    op.create_index(
        "ix_first_interview_plan_source_assessment",
        "first_interview_plan_versions", ["source_assessment_version_id"], unique=False,
    )
    op.create_index(
        "ix_first_interview_plan_versions_status",
        "first_interview_plan_versions", ["status"], unique=False,
    )


def downgrade() -> None:
    op.drop_table("first_interview_plan_versions")

