"""将岗位文档导入的泛化字段迁移为具名字段。

Revision ID: 081_job_document_named_fields
Revises: 080_drop_resume_intake_legacy_json

``SourceDocument.parse_metadata`` 仍由尚未迁移的简历链路使用，因此本迁移只令
岗位文档停止依赖它，不删除共享列。``JobDraft.payload`` 只服务岗位草稿，迁移
已知键后立即删除，避免岗位确认页继续产生无约束业务数据。
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "081_job_document_named_fields"
down_revision = "080_drop_resume_intake_legacy_json"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    """回填有效岗位导入数据，再删除仅属于 JobDraft 的万能 payload 列。"""
    bind = op.get_bind()
    source_columns = _columns("source_documents")
    for name, column in (
        ("parser_provider", sa.Column("parser_provider", sa.String(length=64), nullable=True)),
        ("document_blocks_ref", sa.Column("document_blocks_ref", sa.String(length=512), nullable=True)),
        ("document_blocks_sha256", sa.Column("document_blocks_sha256", sa.String(length=64), nullable=True)),
        ("document_blocks_schema_version", sa.Column("document_blocks_schema_version", sa.String(length=64), nullable=True)),
    ):
        if name not in source_columns:
            op.add_column("source_documents", column)

    draft_columns = _columns("job_drafts")
    additions = (
        ("preset_model_id", sa.Column("preset_model_id", sa.String(length=64), nullable=False, server_default="engineering_experience")),
        ("preset_model_version", sa.Column("preset_model_version", sa.String(length=32), nullable=False, server_default="1.0")),
        ("recommended_preset_model_id", sa.Column("recommended_preset_model_id", sa.String(length=64), nullable=False, server_default="engineering_experience")),
        ("major_requirement_source", sa.Column("major_requirement_source", sa.String(length=64), nullable=True)),
        ("major_requirement_source_quote", sa.Column("major_requirement_source_quote", sa.Text(), nullable=True)),
        ("field_provenance_json", sa.Column("field_provenance_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'"))),
        ("department_auto_created", sa.Column("department_auto_created", sa.Boolean(), nullable=False, server_default=sa.false())),
    )
    for name, column in additions:
        if name not in draft_columns:
            op.add_column("job_drafts", column)

    if "parse_metadata" in source_columns:
        for row in bind.execute(sa.text(
            "SELECT source_document_id, parse_metadata FROM source_documents "
            "WHERE document_type = 'job_requirement'"
        )).mappings():
            metadata = dict(row["parse_metadata"] or {})
            bind.execute(sa.text(
                "UPDATE source_documents SET parser_provider = :provider, "
                "document_blocks_ref = :blocks_ref, document_blocks_sha256 = :blocks_sha, "
                "document_blocks_schema_version = :blocks_version "
                "WHERE source_document_id = :document_id"
            ), {
                "provider": metadata.get("provider"),
                "blocks_ref": metadata.get("document_blocks_ref"),
                "blocks_sha": metadata.get("document_blocks_sha256"),
                "blocks_version": metadata.get("document_blocks_schema_version"),
                "document_id": row["source_document_id"],
            })

    if "payload" in draft_columns:
        for row in bind.execute(sa.text("SELECT job_draft_id, payload FROM job_drafts")).mappings():
            payload = dict(row["payload"] or {})
            extraction = dict(payload.get("extraction_meta") or {})
            bind.execute(sa.text(
                "UPDATE job_drafts SET preset_model_id = :preset_model_id, "
                "preset_model_version = :preset_model_version, "
                "recommended_preset_model_id = :recommended_preset_model_id, "
                "major_requirement_source = :major_source, "
                "major_requirement_source_quote = :major_source_quote, "
                "field_provenance_json = :field_provenance, "
                "department_auto_created = :department_auto_created "
                "WHERE job_draft_id = :job_draft_id"
            ), {
                "preset_model_id": payload.get("preset_model_id") or "engineering_experience",
                "preset_model_version": payload.get("preset_model_version") or "1.0",
                "recommended_preset_model_id": payload.get("recommended_preset_model_id") or payload.get("preset_model_id") or "engineering_experience",
                "major_source": payload.get("major_source"),
                "major_source_quote": payload.get("major_source_quote"),
                "field_provenance": extraction,
                "department_auto_created": bool(payload.get("department_auto_created")),
                "job_draft_id": row["job_draft_id"],
            })
        op.drop_column("job_drafts", "payload")


def downgrade() -> None:
    """仅恢复草稿兼容列；SourceDocument 具名索引不回滚，避免破坏已发布工件。"""
    if "payload" not in _columns("job_drafts"):
        op.add_column(
            "job_drafts", sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'"))
        )