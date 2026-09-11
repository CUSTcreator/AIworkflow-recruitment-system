"""收口题单与面后评估的正式字段合同。

开发阶段无历史业务数据迁移需求：本迁移直接删除已废弃的泛化 payload，
并为执行态题单、原始面评、面评解析和岗位画像建立具名字段。

Revision ID: 083_named_interview_contracts
Revises: 082_job_confirmation_profile_contract
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "083_named_interview_contracts"
down_revision = "082_job_confirmation_profile_contract"
branch_labels = None
depends_on = None

_JSON = sa.JSON()


def upgrade() -> None:
    """开发期硬切换：不回填测试数据，新增具名字段后删除旧 payload。"""
    op.add_column("interview_records", sa.Column("answer_status", sa.String(32), nullable=True))
    op.add_column("interview_records", sa.Column("interviewer_judgement", sa.Text(), nullable=True))
    op.add_column("interview_records", sa.Column("segments_json", _JSON, nullable=False, server_default=sa.text("'[]'")))
    op.add_column("interview_records", sa.Column("source_input_json", _JSON, nullable=False, server_default=sa.text("'{}'")))
    op.drop_column("interview_records", "payload")

    op.add_column("interview_parse_results", sa.Column("parse_schema_version", sa.String(64), nullable=False, server_default="interview_parse_result_v1"))
    for name in ("source_record_ids", "segments_json", "target_decisions_json", "experience_updates_json", "skill_claim_corrections_json", "interview_observations_json", "non_scoring_json", "resolved_interview_target_ids"):
        op.add_column("interview_parse_results", sa.Column(name, _JSON, nullable=False, server_default=sa.text("'[]'")))
    op.add_column("interview_parse_results", sa.Column("parser_version", sa.String(64), nullable=False, server_default=""))
    op.add_column("interview_parse_results", sa.Column("source_hash", sa.String(128), nullable=False, server_default=""))
    op.add_column("interview_parse_results", sa.Column("no_new_evidence", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.drop_column("interview_parse_results", "payload")

    op.add_column("interview_guides", sa.Column("content_json", _JSON, nullable=False, server_default=sa.text("'{}'")))
    op.drop_column("interview_guides", "payload")
    op.add_column("interview_questions", sa.Column("question_json", _JSON, nullable=False, server_default=sa.text("'{}'")))
    op.drop_column("interview_questions", "payload")
    op.drop_column("interviews", "payload")


def downgrade() -> None:
    raise RuntimeError("开发期硬删除迁移不支持降级；请从版本控制恢复数据库模式")
