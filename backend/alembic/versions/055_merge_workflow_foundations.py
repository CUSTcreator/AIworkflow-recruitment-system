"""merge workflow foundation branches

Revision ID: 055_merge_workflow_foundations
Revises: 054_workflow_artifact_v2, 054_job_profile_workflow
"""
from __future__ import annotations

revision = "055_merge_workflow_foundations"
down_revision = ("054_workflow_artifact_v2", "054_job_profile_workflow")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """合并 Artifact 通用归属与岗位画像状态两条独立迁移分支。"""


def downgrade() -> None:
    """合并节点不修改数据库结构。"""