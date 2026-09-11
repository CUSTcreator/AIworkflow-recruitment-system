"""remove deprecated candidate evidence snapshots

Revision ID: 049_remove_candidate_evidence
Revises: 048_replace_risk_targets
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "049_remove_candidate_evidence"
down_revision = "048_replace_risk_targets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """删除冗余快照表，保留能力画像的直接来源引用。"""
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    tables = set(inspector.get_table_names())
    postgres = connection.dialect.name == "postgresql"

    # 1. PostgreSQL 有自引用外键时不能用 batch recreate；改为原地删除对应外键和列。
    if "score_snapshots" in tables:
        score_columns = {item["name"] for item in inspector.get_columns("score_snapshots")}
        if "evidence_snapshot_id" in score_columns:
            if postgres:
                for foreign_key in inspector.get_foreign_keys("score_snapshots"):
                    if "evidence_snapshot_id" in foreign_key.get("constrained_columns", []):
                        op.drop_constraint(foreign_key["name"], "score_snapshots", type_="foreignkey")
                op.drop_column("score_snapshots", "evidence_snapshot_id")
            else:
                with op.batch_alter_table("score_snapshots", recreate="always") as batch:
                    batch.drop_column("evidence_snapshot_id")

    # 2. 能力画像保留简历、岗位、上一版本，并新增面评解析结果这个明确来源。
    if "candidate_capability_profiles" in tables:
        inspector = sa.inspect(connection)
        profile_columns = {item["name"] for item in inspector.get_columns("candidate_capability_profiles")}
        if postgres:
            if "evidence_snapshot_id" in profile_columns:
                for foreign_key in inspector.get_foreign_keys("candidate_capability_profiles"):
                    if "evidence_snapshot_id" in foreign_key.get("constrained_columns", []):
                        op.drop_constraint(foreign_key["name"], "candidate_capability_profiles", type_="foreignkey")
                op.drop_column("candidate_capability_profiles", "evidence_snapshot_id")
            if "source_interview_parse_result_id" not in profile_columns:
                op.add_column(
                    "candidate_capability_profiles",
                    sa.Column("source_interview_parse_result_id", sa.String(length=96), nullable=True),
                )
                op.create_foreign_key(
                    "fk_capability_profile_interview_parse_result",
                    "candidate_capability_profiles", "interview_parse_results",
                    ["source_interview_parse_result_id"], ["parse_result_id"],
                )
                op.create_index(
                    "ix_capability_profile_interview_parse",
                    "candidate_capability_profiles", ["source_interview_parse_result_id"], unique=False,
                )
        else:
            with op.batch_alter_table("candidate_capability_profiles", recreate="always") as batch:
                if "evidence_snapshot_id" in profile_columns:
                    batch.drop_column("evidence_snapshot_id")
                if "source_interview_parse_result_id" not in profile_columns:
                    batch.add_column(sa.Column("source_interview_parse_result_id", sa.String(length=96), nullable=True))
                    batch.create_foreign_key(
                        "fk_capability_profile_interview_parse_result",
                        "interview_parse_results",
                        ["source_interview_parse_result_id"], ["parse_result_id"],
                    )
                    batch.create_index(
                        "ix_capability_profile_interview_parse",
                        ["source_interview_parse_result_id"], unique=False,
                    )

        # 3. 迁移已有面后能力画像：按申请、阶段和版本绑定同次已保存的面评解析结果。
        connection.execute(sa.text(
            "UPDATE candidate_capability_profiles "
            "SET source_interview_parse_result_id = ("
            "  SELECT parse_result_id FROM interview_parse_results "
            "  WHERE interview_parse_results.application_id = candidate_capability_profiles.application_id "
            "    AND interview_parse_results.stage = candidate_capability_profiles.stage "
            "    AND interview_parse_results.version = candidate_capability_profiles.version "
            ") "
            "WHERE stage <> 'screening' "
            "  AND source_interview_parse_result_id IS NULL"
        ))

    # 4. 最后删除快照表；其内容可由 ResumeProfile、能力画像链与面评解析结果重新追溯。
    if "candidate_evidence_snapshots" in set(sa.inspect(connection).get_table_names()):
        op.drop_table("candidate_evidence_snapshots")


def downgrade() -> None:
    raise RuntimeError("049 删除了冗余 CandidateEvidenceSnapshot，不支持自动降级。")


