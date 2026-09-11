"""Candidate scoped resume intake and professional routing.

Revision ID: 035_candidate_intake_routing
Revises: 034_application_intake_lifecycle
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "035_candidate_intake_routing"
down_revision = "034_application_intake_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("job_drafts") as batch:
        batch.add_column(sa.Column("major_requirement", sa.String(length=500), nullable=True))
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("major_requirement", sa.String(length=500), nullable=True))
    with op.batch_alter_table("candidates") as batch:
        batch.add_column(sa.Column("status", sa.String(length=32), nullable=False, server_default="processing"))
        batch.add_column(sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()))
        batch.add_column(sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()))
        batch.create_index("ix_candidates_status", ["status"])
    with op.batch_alter_table("applications") as batch:
        batch.add_column(sa.Column("source_resume_profile_id", sa.String(length=96), nullable=True))
        batch.create_foreign_key("fk_applications_source_resume_profile", "resume_profiles", ["source_resume_profile_id"], ["resume_profile_id"])
        batch.create_index("ix_applications_source_resume_profile_id", ["source_resume_profile_id"])
    with op.batch_alter_table("resume_submissions") as batch:
        batch.alter_column("job_id", existing_type=sa.String(length=64), nullable=True)
        batch.drop_constraint("uq_resume_submission_document_job", type_="unique")


def downgrade() -> None:
    with op.batch_alter_table("resume_submissions") as batch:
        batch.create_unique_constraint("uq_resume_submission_document_job", ["source_document_id", "job_id"])
        batch.alter_column("job_id", existing_type=sa.String(length=64), nullable=False)
    with op.batch_alter_table("applications") as batch:
        batch.drop_index("ix_applications_source_resume_profile_id")
        batch.drop_constraint("fk_applications_source_resume_profile", type_="foreignkey")
        batch.drop_column("source_resume_profile_id")
    with op.batch_alter_table("candidates") as batch:
        batch.drop_index("ix_candidates_status")
        batch.drop_column("updated_at")
        batch.drop_column("created_at")
        batch.drop_column("status")
    with op.batch_alter_table("jobs") as batch:
        batch.drop_column("major_requirement")
    with op.batch_alter_table("job_drafts") as batch:
        batch.drop_column("major_requirement")
