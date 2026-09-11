"""add dynamic workspace documents

Revision ID: 023_workspace_documents
Revises: 022_notification_cursors
"""

from alembic import op
import sqlalchemy as sa

revision = "023_workspace_documents"
down_revision = "022_notification_cursors"
branch_labels = None
depends_on = None


def _workspace_table(name: str, owner_column: str, owner_table: str) -> None:
    op.create_table(
        name,
        sa.Column("workspace_document_id", sa.String(64), primary_key=True),
        sa.Column(owner_column, sa.String(64), sa.ForeignKey(f"{owner_table}.{owner_column}"), nullable=False),
        sa.Column("source_document_id", sa.String(64), sa.ForeignKey("source_documents.source_document_id"), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("uploaded_by", sa.String(64), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(owner_column, "source_document_id", name=f"uq_{name[:-1]}_source"),
    )
    op.create_index(f"ix_{name}_{owner_column}", name, [owner_column])
    op.create_index(f"ix_{name}_source_document_id", name, ["source_document_id"])
    op.create_index(f"ix_{name}_uploaded_by", name, ["uploaded_by"])
    op.create_index(f"ix_{name}_is_active", name, ["is_active"])
    op.create_index(f"ix_{name}_active_order", name, [owner_column, "is_active", "sort_order"])


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "application_workspace_documents" not in tables:
        _workspace_table("application_workspace_documents", "application_id", "applications")
    if "job_workspace_documents" not in tables:
        _workspace_table("job_workspace_documents", "job_id", "jobs")


def downgrade() -> None:
    op.drop_table("job_workspace_documents")
    op.drop_table("application_workspace_documents")
