"""merge workflow step runtime heads

Revision ID: 056_merge_step_runtime
Revises: 055_merge_workflow_foundations, 055_step_runtime_done
"""
from __future__ import annotations

revision = "056_merge_step_runtime"
down_revision = (
    "055_merge_workflow_foundations",
    "055_step_runtime_done",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    """合并两条独立的 Workflow 基础设施迁移分支，不修改业务数据。"""


def downgrade() -> None:
    """合并节点本身不包含数据库结构变更。"""