"""删除剩余泛化数据包列与动态旧表。

开发期硬切换：全部历史数据均为测试数据，不回填、不保留兼容列。
每个保留 JSON 列都具有明确的领域合同：题单 questions_json、评分
result_json、能力画像 capability_json。

Revision ID: 085_remove_remaining_generic_payload_models
Revises: 084_application_hard_screening_named_fields
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "085_remove_remaining_generic_payload_models"
down_revision = "084_application_hard_screening_named_fields"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    """以具名字段取代最后的泛化列，并删除无正式领域职责的旧动态表。"""
    op.add_column("interview_guide_template_versions", sa.Column("questions_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")))
    op.drop_column("interview_guide_template_versions", "payload")

    op.add_column("score_snapshots", sa.Column("result_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    op.drop_column("score_snapshots", "payload")

    op.add_column("candidate_capability_profiles", sa.Column("capability_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    op.drop_column("candidate_capability_profiles", "payload")

    op.drop_column("tasks", "payload")

    for table in (
        "first_interview_progress_drafts",
        "second_interview_progress_drafts",
        "interviewer_raw_notes",
        "question_responses",
        "interview_assessments",
        "interview_evidence",
        "hr_second_review_packages",
        "hr_interview_assessments",
        "final_candidate_review_packages",
    ):
        if _has_table(table):
            op.drop_table(table)


def downgrade() -> None:
    raise RuntimeError("开发期泛化数据包硬删除迁移不支持降级；请从版本控制恢复数据库模式")