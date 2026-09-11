"""add resume document submissions

Revision ID: 011_resume_ingestion
Revises: 010_document_ingestion
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "011_resume_ingestion"
down_revision = "010_document_ingestion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "resume_submissions" in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        "resume_submissions",
        sa.Column("resume_submission_id", sa.String(64), primary_key=True),
        sa.Column(
            "source_document_id",
            sa.String(64),
            sa.ForeignKey("source_documents.source_document_id"),
            nullable=False,
        ),
        sa.Column("job_id", sa.String(64), sa.ForeignKey("jobs.job_id"), nullable=False),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column("external_candidate_id", sa.String(128), nullable=True),
        sa.Column("external_application_id", sa.String(128), nullable=True),
        sa.Column("candidate_name_override", sa.String(128), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "candidate_id",
            sa.String(64),
            sa.ForeignKey("candidates.candidate_id"),
            nullable=True,
        ),
        sa.Column(
            "application_id",
            sa.String(64),
            sa.ForeignKey("applications.application_id"),
            nullable=True,
        ),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("uploaded_by", sa.String(64), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_resume_submission_idempotency_key",
        ),
    )
    for column in (
        "source_document_id",
        "job_id",
        "idempotency_key",
        "external_candidate_id",
        "external_application_id",
        "status",
        "candidate_id",
        "application_id",
        "uploaded_by",
    ):
        op.create_index(
            f"ix_resume_submissions_{column}",
            "resume_submissions",
            [column],
        )
    op.create_index(
        "ix_resume_submissions_job_status",
        "resume_submissions",
        ["job_id", "status"],
    )


def downgrade() -> None:
    op.drop_table("resume_submissions")
