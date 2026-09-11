"""Add application document metadata.

Revision ID: 026_application_documents
Revises: 025_business_permissions
"""

from alembic import op
import sqlalchemy as sa


revision = "026_application_documents"
down_revision = "025_business_permissions"
branch_labels = None
depends_on = None


def _columns() -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns("application_workspace_documents")}


def _indexes() -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("application_workspace_documents")}


def upgrade() -> None:
    existing = _columns()
    for column in (
        sa.Column("category", sa.String(length=32), nullable=False, server_default="other"),
        sa.Column("source_stage", sa.String(length=32), nullable=False, server_default="manual_upload"),
        sa.Column("note", sa.String(length=500), nullable=True),
    ):
        if column.name not in existing:
            op.add_column("application_workspace_documents", column)
    existing_indexes = _indexes()
    for name, columns in (
        ("ix_application_workspace_documents_category", ["category"]),
        ("ix_application_workspace_documents_source_stage", ["source_stage"]),
    ):
        if name not in existing_indexes:
            op.create_index(name, "application_workspace_documents", columns)


def downgrade() -> None:
    existing_indexes = _indexes()
    for name in ("ix_application_workspace_documents_source_stage", "ix_application_workspace_documents_category"):
        if name in existing_indexes:
            op.drop_index(name, table_name="application_workspace_documents")
    existing = _columns()
    for name in ("note", "source_stage", "category"):
        if name in existing:
            op.drop_column("application_workspace_documents", name)
