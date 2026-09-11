"""收口申请与硬筛结果的具名字段合同。

开发期硬切换：不保留 Application / HardScreeningResult 的历史 payload。
申请仅保存硬筛状态投影；规则明细只归属 HardScreeningResult。

Revision ID: 084_application_hard_screening_named_fields
Revises: 083_named_interview_contracts
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "084_application_hard_screening_named_fields"
down_revision = "083_named_interview_contracts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """新增具名硬筛字段后，直接删除开发期测试数据中的泛化列。"""
    op.add_column("applications", sa.Column("hard_screening_policy_id", sa.String(96), nullable=True))
    op.add_column("applications", sa.Column("hard_screening_status", sa.String(32), nullable=True))
    op.add_column("applications", sa.Column("hard_screening_summary", sa.Text(), nullable=True))
    op.add_column("applications", sa.Column("rejection_stage", sa.String(64), nullable=True))
    op.create_foreign_key("fk_applications_hard_screening_policy", "applications", "hard_screening_policies", ["hard_screening_policy_id"], ["policy_id"])
    op.create_index("ix_applications_hard_screening_policy_id", "applications", ["hard_screening_policy_id"])
    op.create_index("ix_applications_hard_screening_status", "applications", ["hard_screening_status"])
    op.create_index("ix_applications_rejection_stage", "applications", ["rejection_stage"])

    op.add_column("hard_screening_results", sa.Column("summary", sa.Text(), nullable=False, server_default=""))
    op.add_column("hard_screening_results", sa.Column("rule_results_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")))
    op.drop_column("hard_screening_results", "payload")
    op.drop_column("applications", "payload")


def downgrade() -> None:
    raise RuntimeError("开发期硬删除迁移不支持降级；请从版本控制恢复数据库模式")