"""Add candidate-scoped workspace document links.

Revision ID: 101_candidate_workspace_documents
Revises: 100_drop_candidate_resume_text
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "101_candidate_workspace_documents"
down_revision = "100_drop_candidate_resume_text"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "candidate_workspace_documents",
        sa.Column("workspace_document_id", sa.String(length=64), primary_key=True),
        sa.Column("candidate_id", sa.String(length=64), sa.ForeignKey("candidates.candidate_id"), nullable=False),
        sa.Column("source_document_id", sa.String(length=64), sa.ForeignKey("source_documents.source_document_id"), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False, server_default="other"),
        sa.Column("source_stage", sa.String(length=32), nullable=False, server_default="manual_upload"),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("uploaded_by", sa.String(length=64), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("candidate_id", "source_document_id", name="uq_candidate_workspace_document_source"),
    )
    op.create_index("ix_candidate_workspace_documents_candidate_id", "candidate_workspace_documents", ["candidate_id"])
    op.create_index("ix_candidate_workspace_documents_source_document_id", "candidate_workspace_documents", ["source_document_id"])
    op.create_index("ix_candidate_workspace_documents_category", "candidate_workspace_documents", ["category"])
    op.create_index("ix_candidate_workspace_documents_source_stage", "candidate_workspace_documents", ["source_stage"])
    op.create_index("ix_candidate_workspace_documents_is_active", "candidate_workspace_documents", ["is_active"])
    op.create_index("ix_candidate_workspace_documents_uploaded_by", "candidate_workspace_documents", ["uploaded_by"])
    op.create_index("ix_candidate_workspace_documents_active_order", "candidate_workspace_documents", ["candidate_id", "is_active", "sort_order"])


def downgrade() -> None:
    op.drop_index("ix_candidate_workspace_documents_active_order", table_name="candidate_workspace_documents")
    for name in (
        "ix_candidate_workspace_documents_uploaded_by",
        "ix_candidate_workspace_documents_is_active",
        "ix_candidate_workspace_documents_source_stage",
        "ix_candidate_workspace_documents_category",
        "ix_candidate_workspace_documents_source_document_id",
        "ix_candidate_workspace_documents_candidate_id",
    ):
        op.drop_index(name, table_name="candidate_workspace_documents")
    op.drop_table("candidate_workspace_documents")
