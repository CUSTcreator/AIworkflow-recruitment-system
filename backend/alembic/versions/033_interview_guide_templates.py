"""Add reusable common interview guide templates.

Revision ID: 033_interview_guide_templates
Revises: 032_permission_only_roles
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "033_interview_guide_templates"
down_revision = "032_permission_only_roles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "interview_guide_templates",
        sa.Column("template_id", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("round", sa.String(length=32), nullable=False, server_default="first"),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.String(length=64), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_interview_guide_templates_round", "interview_guide_templates", ["round"])
    op.create_index("ix_interview_guide_templates_is_default", "interview_guide_templates", ["is_default"])
    op.create_index("ix_interview_guide_templates_is_active", "interview_guide_templates", ["is_active"])
    op.create_index("ix_interview_guide_templates_created_by", "interview_guide_templates", ["created_by"])
    op.create_index(
        "uq_interview_guide_templates_default_round", "interview_guide_templates", ["round"],
        unique=True,
        postgresql_where=sa.text("is_default = true AND is_active = true"),
        sqlite_where=sa.text("is_default = 1 AND is_active = 1"),
    )
    op.create_table(
        "interview_guide_template_versions",
        sa.Column("template_version_id", sa.String(length=64), primary_key=True),
        sa.Column("template_id", sa.String(length=64), sa.ForeignKey("interview_guide_templates.template_id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("source_document_id", sa.String(length=64), sa.ForeignKey("source_documents.source_document_id"), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=64), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("template_id", "version", name="uq_interview_guide_template_version"),
    )
    op.create_index("ix_interview_guide_template_versions_template_id", "interview_guide_template_versions", ["template_id"])
    op.create_index("ix_interview_guide_template_versions_status", "interview_guide_template_versions", ["template_id", "status"])
    op.create_index("ix_interview_guide_template_versions_source_document_id", "interview_guide_template_versions", ["source_document_id"])
    op.create_index("ix_interview_guide_template_versions_created_by", "interview_guide_template_versions", ["created_by"])
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("common_interview_template_id", sa.String(length=64), nullable=True))
        batch.create_foreign_key(
            "fk_jobs_common_interview_template", "interview_guide_templates",
            ["common_interview_template_id"], ["template_id"],
        )
        batch.create_index("ix_jobs_common_interview_template_id", ["common_interview_template_id"])


def downgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.drop_index("ix_jobs_common_interview_template_id")
        batch.drop_constraint("fk_jobs_common_interview_template", type_="foreignkey")
        batch.drop_column("common_interview_template_id")
    op.drop_table("interview_guide_template_versions")
    op.drop_table("interview_guide_templates")
