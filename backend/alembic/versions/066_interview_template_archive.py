"""add interview guide template archive fields

Revision ID: 066_interview_template_archive
Revises: 065_add_user_soft_deletion
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "066_interview_template_archive"
down_revision = "065_add_user_soft_deletion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """为版本化通用题单补归档审计字段；归档不删除任何历史版本。"""
    inspector = sa.inspect(op.get_bind())
    columns = {item["name"] for item in inspector.get_columns("interview_guide_templates")}
    indexes = {item["name"] for item in inspector.get_indexes("interview_guide_templates")}
    foreign_keys = {item.get("name") for item in inspector.get_foreign_keys("interview_guide_templates")}
    with op.batch_alter_table("interview_guide_templates") as batch:
        if "archived_at" not in columns:
            batch.add_column(sa.Column("archived_at", sa.DateTime(), nullable=True))
        if "archived_by_user_id" not in columns:
            batch.add_column(sa.Column("archived_by_user_id", sa.String(length=64), nullable=True))
        if "fk_interview_guide_templates_archived_by_user_id" not in foreign_keys:
            batch.create_foreign_key(
                "fk_interview_guide_templates_archived_by_user_id",
                "users", ["archived_by_user_id"], ["user_id"],
            )
        if "ix_interview_guide_templates_archived_at" not in indexes:
            batch.create_index("ix_interview_guide_templates_archived_at", ["archived_at"])
        if "ix_interview_guide_templates_archived_by_user_id" not in indexes:
            batch.create_index("ix_interview_guide_templates_archived_by_user_id", ["archived_by_user_id"])


def downgrade() -> None:
    """回滚题单归档字段。"""
    inspector = sa.inspect(op.get_bind())
    columns = {item["name"] for item in inspector.get_columns("interview_guide_templates")}
    indexes = {item["name"] for item in inspector.get_indexes("interview_guide_templates")}
    foreign_keys = {item.get("name") for item in inspector.get_foreign_keys("interview_guide_templates")}
    with op.batch_alter_table("interview_guide_templates") as batch:
        if "ix_interview_guide_templates_archived_by_user_id" in indexes:
            batch.drop_index("ix_interview_guide_templates_archived_by_user_id")
        if "ix_interview_guide_templates_archived_at" in indexes:
            batch.drop_index("ix_interview_guide_templates_archived_at")
        if "fk_interview_guide_templates_archived_by_user_id" in foreign_keys:
            batch.drop_constraint("fk_interview_guide_templates_archived_by_user_id", type_="foreignkey")
        if "archived_by_user_id" in columns:
            batch.drop_column("archived_by_user_id")
        if "archived_at" in columns:
            batch.drop_column("archived_at")