"""add user soft deletion fields

Revision ID: 065_add_user_soft_deletion
Revises: 064_job_document_hash_reimport
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "065_add_user_soft_deletion"
down_revision = "064_job_document_hash_reimport"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """补齐 User 实体已使用的逻辑删除列，使重建容器可安全启动。"""
    inspector = sa.inspect(op.get_bind())
    columns = {item["name"] for item in inspector.get_columns("users")}
    indexes = {item["name"] for item in inspector.get_indexes("users")}
    foreign_keys = {item.get("name") for item in inspector.get_foreign_keys("users")}
    with op.batch_alter_table("users") as batch:
        if "deleted_at" not in columns:
            batch.add_column(sa.Column("deleted_at", sa.DateTime(), nullable=True))
        if "deleted_by_user_id" not in columns:
            batch.add_column(sa.Column("deleted_by_user_id", sa.String(length=64), nullable=True))
        if "fk_users_deleted_by_user_id" not in foreign_keys:
            batch.create_foreign_key(
                "fk_users_deleted_by_user_id", "users", ["deleted_by_user_id"], ["user_id"]
            )
        if "ix_users_deleted_at" not in indexes:
            batch.create_index("ix_users_deleted_at", ["deleted_at"])
        if "ix_users_deleted_by_user_id" not in indexes:
            batch.create_index("ix_users_deleted_by_user_id", ["deleted_by_user_id"])


def downgrade() -> None:
    """回滚 User 逻辑删除字段。"""
    inspector = sa.inspect(op.get_bind())
    columns = {item["name"] for item in inspector.get_columns("users")}
    indexes = {item["name"] for item in inspector.get_indexes("users")}
    foreign_keys = {item.get("name") for item in inspector.get_foreign_keys("users")}
    with op.batch_alter_table("users") as batch:
        if "ix_users_deleted_by_user_id" in indexes:
            batch.drop_index("ix_users_deleted_by_user_id")
        if "ix_users_deleted_at" in indexes:
            batch.drop_index("ix_users_deleted_at")
        if "fk_users_deleted_by_user_id" in foreign_keys:
            batch.drop_constraint("fk_users_deleted_by_user_id", type_="foreignkey")
        if "deleted_by_user_id" in columns:
            batch.drop_column("deleted_by_user_id")
        if "deleted_at" in columns:
            batch.drop_column("deleted_at")