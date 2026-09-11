"""add named resume intake contract fields

Revision ID: 074_resume_intake_named_contract_fields
Revises: 073_activity_checkpoints

该迁移是简历导入从泛化 payload 迁移到具名合同字段的第一阶段：只新增字段并回填，
不删除旧字段。旧字段删除必须在所有 Workflow、读模型和历史数据验证完成后单独进行。
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "074_resume_intake_contracts"
down_revision = "073_activity_checkpoints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("resume_submissions") as batch:
        batch.add_column(sa.Column("parse_result_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
        batch.add_column(sa.Column("structure_result_ref", sa.String(length=512), nullable=True))
        batch.add_column(sa.Column("structure_result_sha256", sa.String(length=128), nullable=True))
        batch.add_column(sa.Column("structure_schema_version", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("structure_review_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
        batch.add_column(sa.Column("review_context_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
        batch.add_column(sa.Column("routing_status", sa.String(length=48), nullable=False, server_default="idle"))
        batch.add_column(sa.Column("routing_reason", sa.Text(), nullable=True))
        batch.add_column(sa.Column("routing_result_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
        batch.add_column(sa.Column("routing_workflow_run_id", sa.String(length=64), nullable=True))
        batch.create_index("ix_resume_submissions_routing_status", ["routing_status"])
        batch.create_index("ix_resume_submissions_routing_workflow_run_id", ["routing_workflow_run_id"])
        batch.create_foreign_key(
            "fk_resume_submissions_routing_workflow_run",
            "workflow_runs",
            ["routing_workflow_run_id"],
            ["workflow_run_id"],
        )

    with op.batch_alter_table("resume_profiles") as batch:
        batch.add_column(sa.Column("profile_schema_version", sa.String(length=64), nullable=False, server_default="resume_profile_v1_1"))
        batch.add_column(sa.Column("profile_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))

    # 当前仓库的历史数据均为测试/开发数据；仍保留最小回填，避免升级后旧 Profile
    # 立刻无法被 V1 读取。路由 payload 的复杂历史形状由应用层只读回退负责。
    op.execute("UPDATE resume_profiles SET profile_json = payload")
    op.execute("UPDATE resume_submissions SET parse_result_json = parse_metadata")


def downgrade() -> None:
    with op.batch_alter_table("resume_profiles") as batch:
        batch.drop_column("profile_json")
        batch.drop_column("profile_schema_version")
    with op.batch_alter_table("resume_submissions") as batch:
        batch.drop_constraint("fk_resume_submissions_routing_workflow_run", type_="foreignkey")
        batch.drop_index("ix_resume_submissions_routing_workflow_run_id")
        batch.drop_index("ix_resume_submissions_routing_status")
        batch.drop_column("routing_workflow_run_id")
        batch.drop_column("routing_result_json")
        batch.drop_column("routing_reason")
        batch.drop_column("routing_status")
        batch.drop_column("review_context_json")
        batch.drop_column("structure_review_json")
        batch.drop_column("structure_schema_version")
        batch.drop_column("structure_result_sha256")
        batch.drop_column("structure_result_ref")
        batch.drop_column("parse_result_json")
