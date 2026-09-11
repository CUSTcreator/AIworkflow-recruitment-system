"""allow terminal job document reimport by hash

Revision ID: 064_job_document_hash_reimport
Revises: 063_merge_job_draft_lines
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "064_job_document_hash_reimport"
down_revision = "063_merge_job_draft_lines"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """哈希保留为查询索引，不再把它当作所有历史导入批次的唯一键。"""
    inspector = sa.inspect(op.get_bind())
    constraints = {item["name"] for item in inspector.get_unique_constraints("source_documents")}
    indexes = {item["name"] for item in inspector.get_indexes("source_documents")}
    with op.batch_alter_table("source_documents") as batch:
        if "uq_source_document_type_sha256" in constraints:
            batch.drop_constraint("uq_source_document_type_sha256", type_="unique")
        if "ix_source_documents_type_sha256" not in indexes:
            batch.create_index("ix_source_documents_type_sha256", ["document_type", "source_sha256"])


def downgrade() -> None:
    """恢复旧约束前要求运维方先处理升级后产生的重复历史记录。"""
    inspector = sa.inspect(op.get_bind())
    indexes = {item["name"] for item in inspector.get_indexes("source_documents")}
    with op.batch_alter_table("source_documents") as batch:
        if "ix_source_documents_type_sha256" in indexes:
            batch.drop_index("ix_source_documents_type_sha256")
        batch.create_unique_constraint(
            "uq_source_document_type_sha256", ["document_type", "source_sha256"]
        )