"""enforce runtime non-null contract

Revision ID: 069_enforce_runtime_not_null
Revises: 068_hr_system_admin_perm
Create Date: 2026-08-24
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "069_enforce_runtime_not_null"
down_revision = "068_hr_system_admin_perm"
branch_labels = None
depends_on = None


# ORM 已把这些字段声明为非空，但历史迁移没有同步到 PostgreSQL。它们承载当前
# 读模型和工作流发布的必要字段，继续允许 NULL 会让写入成功后在读取阶段崩溃。
_NON_NULL_COLUMNS: tuple[tuple[str, str], ...] = (
    ("jobs", "responsibilities"),
    ("jobs", "qualifications"),
    ("job_versions", "snapshot"),
    ("applications", "jd_version_id"),
    ("screening_assessments", "screening_assessment_id"),
    ("screening_assessments", "application_id"),
    ("screening_assessments", "version"),
    ("screening_assessments", "score_status"),
    ("screening_assessments", "qualification_gate"),
    ("screening_assessments", "summary"),
    ("screening_assessments", "screening_result_view"),
    ("screening_assessments", "decision_overview"),
    ("screening_assessments", "failure_details"),
    ("screening_assessments", "created_at"),
    ("screening_assessments", "updated_at"),
    ("interview_targets", "application_id"),
    ("interview_targets", "interview_target_id"),
    ("interview_targets", "purpose"),
    ("interview_targets", "target_type"),
    ("interview_targets", "target_id"),
    ("interview_targets", "title"),
    ("interview_targets", "verification_goal"),
    ("interview_targets", "trigger_code"),
    ("interview_targets", "status"),
    ("interview_targets", "stage_created"),
    ("interview_targets", "source_result_ids"),
    ("interview_targets", "evidence_ids"),
    ("interview_targets", "attributes"),
    ("interview_targets", "updated_at"),
    ("interview_guides", "application_id"),
    ("interview_guides", "payload"),
    ("interview_guides", "created_at"),
    ("interview_questions", "application_id"),
    ("interview_questions", "payload"),
    ("interview_questions", "created_at"),
)


def _assert_no_nulls(table_name: str, column_name: str) -> None:
    """避免“为了迁移成功”而猜测或删除历史业务数据。"""
    count = op.get_bind().execute(
        sa.text(f"SELECT count(*) FROM {table_name} WHERE {column_name} IS NULL")
    ).scalar_one()
    if count:
        raise RuntimeError(
            f"cannot_enforce_not_null:{table_name}.{column_name}:null_rows={count}"
        )


def upgrade() -> None:
    """在已核验历史数据后，使数据库约束与 ORM 运行时合同保持一致。"""
    for table_name, column_name in _NON_NULL_COLUMNS:
        _assert_no_nulls(table_name, column_name)
        op.alter_column(table_name, column_name, nullable=False)


def downgrade() -> None:
    """只撤销数据库约束；不改写已有业务数据。"""
    for table_name, column_name in reversed(_NON_NULL_COLUMNS):
        op.alter_column(table_name, column_name, nullable=True)
