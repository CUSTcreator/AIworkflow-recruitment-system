"""补齐岗位和简历画像的具名 JSON 字段。

Revision ID: 079_reconcile_profile_json_schema
Revises: 078_reconcile_screening_v1_source_contract

历史环境曾出现 Alembic 版本已前进、实际表却缺少 profile_json 的部署漂移。
本迁移可重复执行：仅在字段缺失时新增，并从保留的 payload 兼容副本回填。
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "079_reconcile_profile_json_schema"
down_revision = "078_reconcile_screening_v1_source_contract"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    """为已有和新建环境统一补齐 profile_json，并保留 payload 历史兼容数据。"""
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    for table in ("resume_profiles", "job_requirement_profiles"):
        if table not in tables or "profile_json" in _columns(table):
            continue
        op.add_column(
            table,
            sa.Column("profile_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        )
        if "payload" in _columns(table):
            op.execute(f"UPDATE {table} SET profile_json = payload WHERE payload IS NOT NULL")


def downgrade() -> None:
    """不删除兼容字段，避免回滚时破坏已发布评分版本的正式来源。"""