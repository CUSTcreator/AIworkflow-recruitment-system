"""补齐已在部署数据库执行、但曾遗漏提交的迁移版本。

Revision ID: 078_reconcile_screening_v1_source_contract
Revises: 076_merge_legacy_resume_and_first_interview_drafts
Create Date: 2026-08-26

该兼容迁移不执行 DDL：目标数据库已保存此版本号。保留它是为了让
Alembic 能正确识别当前版本，并继续执行后续迁移。
"""

from __future__ import annotations


revision = "078_reconcile_screening_v1_source_contract"
down_revision = "076_merge_legacy_resume_and_first_interview_drafts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """数据库已执行过原迁移，此处仅补齐源码中的迁移链。"""


def downgrade() -> None:
    """兼容迁移没有独立 DDL，因此无需回滚动作。"""

