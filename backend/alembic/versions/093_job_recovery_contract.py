"""Separate file assets from job-import business state.

Revision ID: 093_job_recovery_contract
Revises: 092_merge_resume_recovery_heads

SourceDocument becomes an immutable file record. Existing job-document state is
backfilled into JobDocumentImport before the legacy process columns are removed.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "093_job_recovery_contract"
down_revision = "092_merge_resume_recovery_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_document_imports",
        sa.Column("job_document_import_id", sa.String(length=64), nullable=False),
        sa.Column("source_document_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("review_kind", sa.String(length=48), nullable=True),
        sa.Column("failure_kind", sa.String(length=48), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("recovery_code", sa.String(length=64), nullable=True),
        sa.Column("recovery_context_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("workflow_run_id", sa.String(length=64), nullable=True),
        sa.Column("uploaded_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["source_document_id"], ["source_documents.source_document_id"]),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.workflow_run_id"]),
        sa.ForeignKeyConstraint(["uploaded_by"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("job_document_import_id"),
        sa.UniqueConstraint("source_document_id", name="uq_job_document_import_source"),
    )
    op.create_index("ix_job_document_imports_source_document_id", "job_document_imports", ["source_document_id"])
    op.create_index("ix_job_document_imports_status", "job_document_imports", ["status"])
    op.create_index("ix_job_document_imports_review_kind", "job_document_imports", ["review_kind"])
    op.create_index("ix_job_document_imports_failure_kind", "job_document_imports", ["failure_kind"])
    op.create_index("ix_job_document_imports_recovery_code", "job_document_imports", ["recovery_code"])
    op.create_index("ix_job_document_imports_workflow_run_id", "job_document_imports", ["workflow_run_id"])
    op.create_index("ix_job_document_imports_uploaded_by", "job_document_imports", ["uploaded_by"])

    # 093 was not part of the previous schema line, so recovery columns may not
    # yet exist. Backfill the state fields that do exist on SourceDocument.
    op.execute(
        """
        INSERT INTO job_document_imports (
            job_document_import_id, source_document_id, status, error_message,
            uploaded_by, created_at, updated_at, completed_at
        )
        SELECT
            'JIMP_' || md5(source_document_id),
            source_document_id,
            CASE
                WHEN status = 'confirmed' THEN 'completed'
                WHEN status IN ('parsing', 'extracting') THEN 'processing'
                WHEN status IN ('review_required', 'failed') THEN status
                ELSE 'queued'
            END,
            error_message,
            uploaded_by,
            created_at,
            updated_at,
            CASE WHEN status = 'confirmed' THEN updated_at ELSE NULL END
        FROM source_documents
        WHERE document_type = 'job_requirement'
        """
    )
    op.execute(
        """
        UPDATE job_document_imports
        SET workflow_run_id = (
            SELECT workflow_run_id
            FROM workflow_runs
            WHERE workflow_type = 'job_document_import_workflow'
              AND subject_type = 'source_document'
              AND subject_id = job_document_imports.source_document_id
            ORDER BY started_at DESC
            LIMIT 1
        )
        """
    )
    op.execute(
        """
        UPDATE workflow_runs
        SET subject_type = 'job_document_import',
            subject_id = (
                SELECT job_document_import_id
                FROM job_document_imports
                WHERE source_document_id = workflow_runs.subject_id
            )
        WHERE workflow_type = 'job_document_import_workflow'
          AND subject_type = 'source_document'
          AND EXISTS (
              SELECT 1 FROM job_document_imports
              WHERE source_document_id = workflow_runs.subject_id
          )
        """
    )

    op.add_column("job_versions", sa.Column("recovery_code", sa.String(length=64), nullable=True))
    op.add_column("job_versions", sa.Column("recovery_context_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    op.create_index("ix_job_versions_recovery_code", "job_versions", ["recovery_code"])
    op.alter_column("job_versions", "recovery_context_json", server_default=None)

    op.drop_index("ix_source_documents_type_status", table_name="source_documents")
    op.drop_index("ix_source_documents_status", table_name="source_documents")
    op.drop_column("source_documents", "error_message")
    op.drop_column("source_documents", "status")


def downgrade() -> None:
    op.add_column("source_documents", sa.Column("status", sa.String(length=32), nullable=False, server_default="uploaded"))
    op.add_column("source_documents", sa.Column("error_message", sa.Text(), nullable=True))
    op.create_index("ix_source_documents_status", "source_documents", ["status"])
    op.create_index("ix_source_documents_type_status", "source_documents", ["document_type", "status"])
    op.execute(
        """
        UPDATE source_documents
        SET status = (
                SELECT CASE
                    WHEN status = 'completed' THEN 'confirmed'
                    WHEN status = 'processing' THEN 'parsing'
                    WHEN status = 'partially_confirmed' THEN 'review_required'
                    ELSE status
                END
                FROM job_document_imports
                WHERE source_document_id = source_documents.source_document_id
            ),
            error_message = (
                SELECT error_message FROM job_document_imports
                WHERE source_document_id = source_documents.source_document_id
            )
        WHERE EXISTS (
            SELECT 1 FROM job_document_imports
            WHERE source_document_id = source_documents.source_document_id
        )
        """
    )
    op.execute(
        """
        UPDATE workflow_runs
        SET subject_type = 'source_document',
            subject_id = (
                SELECT source_document_id
                FROM job_document_imports
                WHERE job_document_import_id = workflow_runs.subject_id
            )
        WHERE workflow_type = 'job_document_import_workflow'
          AND subject_type = 'job_document_import'
        """
    )
    op.drop_index("ix_job_versions_recovery_code", table_name="job_versions")
    op.drop_column("job_versions", "recovery_context_json")
    op.drop_column("job_versions", "recovery_code")
    op.drop_table("job_document_imports")
