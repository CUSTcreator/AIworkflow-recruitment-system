"""恢复已部署数据库使用的迁移版本标识。

数据库已处于此版本；该合并节点不执行额外 DDL，只让 Alembic 能够继续解析既有迁移链。
"""
from __future__ import annotations

revision = "076_merge_legacy_resume_and_first_interview_drafts"
down_revision = "075_resume_structure_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """合并历史简历与一面题单草稿分支；数据库已完成对应结构变更。"""


def downgrade() -> None:
    """合并节点不包含独立结构变更。"""

