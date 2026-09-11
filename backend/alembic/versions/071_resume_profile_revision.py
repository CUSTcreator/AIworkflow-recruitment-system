"""add revision to resume profiles

Revision ID: 071_resume_profile_revision
Revises: 070_job_profile_gating
Create Date: 2026-08-24

补齐 ResumeProfileRecord 与数据库表的版本契约。此前 ORM 已读取
``resume_profiles.revision``，但历史迁移遗漏该列，导致人工选岗等读取当前
简历画像的接口在已有数据库上返回 500。
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "071_resume_profile_revision"
down_revision = "070_job_profile_gating"
branch_labels = None
depends_on = None


def _columns(table_name: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table_name)}


def upgrade() -> None:
    """为既有简历画像回填首个修订号，并保证后续 ORM 查询可用。"""
    if "revision" in _columns("resume_profiles"):
        return
    with op.batch_alter_table("resume_profiles") as batch:
        batch.add_column(
            sa.Column("revision", sa.Integer(), nullable=False, server_default="1")
        )


def downgrade() -> None:
    """仅撤回本次补齐字段；生产环境不应依赖降级删除历史修订信息。"""
    if "revision" not in _columns("resume_profiles"):
        return
    with op.batch_alter_table("resume_profiles") as batch:
        batch.drop_column("revision")
