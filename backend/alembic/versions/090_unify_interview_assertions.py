"""将面评解析收敛为单一 assertions 字段。

Revision ID: 090_unify_interview_assertions
Revises: 089_normalize_assessment_topology
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "090_unify_interview_assertions"
down_revision = "089_normalize_assessment_topology"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "interview_parse_results",
        sa.Column("assertions_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )
    for name in (
        "target_decisions_json",
        "experience_updates_json",
        "skill_claim_corrections_json",
        "interview_observations_json",
        "non_scoring_json",
    ):
        op.drop_column("interview_parse_results", name)


def downgrade() -> None:
    raise RuntimeError("开发期硬切换迁移不支持降级；请从版本控制恢复数据库模式")
