"""Reconcile the document-ingestion schema with the named state contracts.

Revision ID: 095_reconcile_document_import_schema
Revises: 094_application_recovery

The database was stamped at 094 while the physical schema still contained the
pre-093 SourceDocument process columns and did not contain JobDocumentImport.
This forward-only repair makes the migration idempotent enough to handle that
mixed state without resetting any business tables.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "095_reconcile_document_import_schema"
down_revision = "094_application_recovery"
branch_labels = None
depends_on = None


def _tables(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def _columns(bind, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(table)}


def _indexes(bind, table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(bind).get_indexes(table)}


def _create_job_document_imports(bind) -> None:
    """Create the current job-import state owner when 093 was only stamped."""

    if "job_document_imports" in _tables(bind):
        return
    op.create_table(
        "job_document_imports",
        sa.Column("job_document_import_id", sa.String(length=64), nullable=False),
        sa.Column("source_document_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("review_kind", sa.String(length=48), nullable=True),
        sa.Column("failure_kind", sa.String(length=48), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("recovery_code", sa.String(length=64), nullable=True),
        sa.Column(
            "recovery_context_json",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("workflow_run_id", sa.String(length=64), nullable=True),
        sa.Column("uploaded_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["source_document_id"], ["source_documents.source_document_id"]
        ),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"], ["workflow_runs.workflow_run_id"]
        ),
        sa.ForeignKeyConstraint(["uploaded_by"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("job_document_import_id"),
        sa.UniqueConstraint("source_document_id", name="uq_job_document_import_source"),
    )


def _ensure_indexes(bind) -> None:
    indexes = _indexes(bind, "job_document_imports")
    definitions = {
        "ix_job_document_imports_source_document_id": ["source_document_id"],
        "ix_job_document_imports_status": ["status"],
        "ix_job_document_imports_review_kind": ["review_kind"],
        "ix_job_document_imports_failure_kind": ["failure_kind"],
        "ix_job_document_imports_recovery_code": ["recovery_code"],
        "ix_job_document_imports_workflow_run_id": ["workflow_run_id"],
        "ix_job_document_imports_uploaded_by": ["uploaded_by"],
    }
    for name, columns in definitions.items():
        if name not in indexes:
            op.create_index(name, "job_document_imports", columns)


def _backfill_job_imports(bind) -> None:
    """Move legacy job-document state before the old columns are removed."""

    source_columns = _columns(bind, "source_documents")
    status_expr = (
        "CASE "
        "WHEN status = 'confirmed' THEN 'completed' "
        "WHEN status IN ('parsing', 'extracting') THEN 'processing' "
        "WHEN status IN ('review_required', 'failed') THEN status "
        "ELSE 'queued' END"
        if "status" in source_columns
        else "'queued'"
    )
    error_expr = "error_message" if "error_message" in source_columns else "NULL"
    recovery_code_expr = (
        "recovery_code" if "recovery_code" in source_columns else "NULL"
    )
    recovery_context_expr = (
        "recovery_context_json"
        if "recovery_context_json" in source_columns
        else "'{}'::json"
    )
    bind.execute(
        sa.text(
            f"""
            INSERT INTO job_document_imports (
                job_document_import_id, source_document_id, status, error_message,
                recovery_code, recovery_context_json, uploaded_by,
                created_at, updated_at, completed_at
            )
            SELECT
                'JIMP_' || md5(source_document_id),
                source_document_id,
                {status_expr},
                {error_expr},
                {recovery_code_expr},
                {recovery_context_expr},
                uploaded_by,
                created_at,
                updated_at,
                CASE WHEN {status_expr} = 'completed' THEN updated_at ELSE NULL END
            FROM source_documents
            WHERE document_type = 'job_requirement'
            ON CONFLICT (source_document_id) DO NOTHING
            """
        )
    )

    # Preserve the latest workflow association and move its subject to the new
    # state owner before SourceDocument's legacy status columns disappear.
    bind.execute(
        sa.text(
            """
            UPDATE job_document_imports AS imports
            SET workflow_run_id = runs.workflow_run_id
            FROM workflow_runs AS runs
            WHERE imports.workflow_run_id IS NULL
              AND runs.workflow_type = 'job_document_import_workflow'
              AND runs.subject_type = 'source_document'
              AND runs.subject_id = imports.source_document_id
              AND runs.started_at = (
                  SELECT MAX(candidate.started_at)
                  FROM workflow_runs AS candidate
                  WHERE candidate.workflow_type = 'job_document_import_workflow'
                    AND candidate.subject_type = 'source_document'
                    AND candidate.subject_id = imports.source_document_id
              )
            """
        )
    )
    bind.execute(
        sa.text(
            """
            UPDATE workflow_runs AS runs
            SET subject_type = 'job_document_import',
                subject_id = imports.job_document_import_id
            FROM job_document_imports AS imports
            WHERE runs.workflow_type = 'job_document_import_workflow'
              AND runs.subject_type = 'source_document'
              AND runs.subject_id = imports.source_document_id
            """
        )
    )


def _drop_legacy_source_columns(bind) -> None:
    """Remove process state from the immutable file asset table."""

    columns = _columns(bind, "source_documents")
    indexes = _indexes(bind, "source_documents")
    for name in ("ix_source_documents_type_status", "ix_source_documents_status"):
        if name in indexes:
            op.drop_index(name, table_name="source_documents")
    for name in (
        "status",
        "error_message",
        "recovery_code",
        "recovery_context_json",
    ):
        if name in columns:
            op.drop_column("source_documents", name)


def upgrade() -> None:
    bind = op.get_bind()
    _create_job_document_imports(bind)
    _ensure_indexes(bind)
    _backfill_job_imports(bind)
    _drop_legacy_source_columns(bind)


def downgrade() -> None:
    raise RuntimeError(
        "095 仅用于修复已漂移的文档表结构，不支持自动降级；请从备份恢复。"
    )
