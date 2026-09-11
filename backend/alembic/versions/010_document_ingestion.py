"""add source document ingestion

Revision ID: 010_document_ingestion
Revises: 009_remove_second_planning
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "010_document_ingestion"
down_revision = "009_remove_second_planning"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "source_documents" not in tables:
        op.create_table(
        "source_documents",
        sa.Column("source_document_id", sa.String(64), primary_key=True),
        sa.Column("document_type", sa.String(32), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(128), nullable=False),
        sa.Column("object_ref", sa.String(512), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("parsed_text", sa.Text(), nullable=True),
        sa.Column("parse_metadata", sa.JSON(), nullable=False),
        sa.Column("external_document_id", sa.String(128), nullable=True),
        sa.Column("uploaded_by", sa.String(64), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "document_type",
            "source_sha256",
            name="uq_source_document_type_sha256",
        ),
        )
        op.create_index(
            "ix_source_documents_document_type",
            "source_documents",
            ["document_type"],
        )
        op.create_index("ix_source_documents_source_sha256", "source_documents", ["source_sha256"])
        op.create_index("ix_source_documents_status", "source_documents", ["status"])
        op.create_index(
            "ix_source_documents_external_document_id",
            "source_documents",
            ["external_document_id"],
        )
        op.create_index("ix_source_documents_uploaded_by", "source_documents", ["uploaded_by"])
        op.create_index(
            "ix_source_documents_type_status",
            "source_documents",
            ["document_type", "status"],
        )

    if "job_drafts" not in tables:
        op.create_table(
        "job_drafts",
        sa.Column("job_draft_id", sa.String(64), primary_key=True),
        sa.Column(
            "source_document_id",
            sa.String(64),
            sa.ForeignKey("source_documents.source_document_id"),
            nullable=False,
        ),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(128), nullable=False),
        sa.Column("headcount", sa.Integer(), nullable=True),
        sa.Column("responsibilities", sa.JSON(), nullable=False),
        sa.Column("qualifications", sa.JSON(), nullable=False),
        sa.Column("education_requirement", sa.String(255), nullable=True),
        sa.Column(
            "department_id",
            sa.String(64),
            sa.ForeignKey("departments.department_id"),
            nullable=False,
        ),
        sa.Column("source_text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "confirmed_job_id",
            sa.String(64),
            sa.ForeignKey("jobs.job_id"),
            nullable=True,
        ),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "source_document_id",
            "sequence_no",
            name="uq_job_draft_document_sequence",
        ),
        )
        op.create_index("ix_job_drafts_source_document_id", "job_drafts", ["source_document_id"])
        op.create_index("ix_job_drafts_department_id", "job_drafts", ["department_id"])
        op.create_index("ix_job_drafts_status", "job_drafts", ["status"])
        op.create_index("ix_job_drafts_confirmed_job_id", "job_drafts", ["confirmed_job_id"])

    workflow_columns = {
        item["name"] for item in sa.inspect(bind).get_columns("workflow_runs")
    }
    with op.batch_alter_table("workflow_runs") as batch:
        batch.alter_column("application_id", existing_type=sa.String(64), nullable=True)
        if "subject_type" not in workflow_columns:
            batch.add_column(sa.Column("subject_type", sa.String(32), nullable=True))
        if "subject_id" not in workflow_columns:
            batch.add_column(sa.Column("subject_id", sa.String(64), nullable=True))
    op.execute(
        """
        UPDATE workflow_runs
        SET subject_type = 'application',
            subject_id = application_id
        WHERE application_id IS NOT NULL
          AND subject_type IS NULL
        """
    )
    workflow_indexes = {
        item["name"] for item in sa.inspect(bind).get_indexes("workflow_runs")
    }
    if "ix_workflow_runs_subject_type" not in workflow_indexes:
        op.create_index("ix_workflow_runs_subject_type", "workflow_runs", ["subject_type"])
    if "ix_workflow_runs_subject_id" not in workflow_indexes:
        op.create_index("ix_workflow_runs_subject_id", "workflow_runs", ["subject_id"])
    if "ix_workflow_runs_subject_type_run" not in workflow_indexes:
        op.create_index(
            "ix_workflow_runs_subject_type_run",
            "workflow_runs",
            ["subject_type", "subject_id", "workflow_type", "started_at"],
        )


def downgrade() -> None:
    op.drop_index("ix_workflow_runs_subject_type_run", table_name="workflow_runs")
    op.drop_index("ix_workflow_runs_subject_id", table_name="workflow_runs")
    op.drop_index("ix_workflow_runs_subject_type", table_name="workflow_runs")
    with op.batch_alter_table("workflow_runs") as batch:
        batch.drop_column("subject_id")
        batch.drop_column("subject_type")
        batch.alter_column("application_id", existing_type=sa.String(64), nullable=False)
    op.drop_table("job_drafts")
    op.drop_table("source_documents")
