"""将评分拓扑由过渡 JSON 收敛为规范化实体表。

Revision ID: 089_normalize_assessment_topology
Revises: 088_assessment_topology_snapshots

本迁移只新增表和列，不删除 topology_json。新发布的 V1 以规范化表为唯一
业务写入目标；旧 JSON 列待读取路径完全切换并经人工确认后再单独清理。
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "089_normalize_assessment_topology"
down_revision = "088_assessment_topology_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. V1 固化的结构层：节点、边和引用以后不随 V2/V3 分数变化而改变。
    op.create_table(
        "assessment_topology_definitions",
        sa.Column("topology_definition_id", sa.String(length=96), nullable=False),
        sa.Column("root_assessment_version_id", sa.String(length=96), nullable=False),
        sa.Column("application_id", sa.String(length=64), nullable=False),
        sa.Column(
            "structure_schema_version",
            sa.String(length=64),
            nullable=False,
            server_default="assessment_topology_definition_v1",
        ),
        sa.Column("structure_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["root_assessment_version_id"],
            ["application_assessment_versions.assessment_version_id"],
        ),
        sa.ForeignKeyConstraint(["application_id"], ["applications.application_id"]),
        sa.PrimaryKeyConstraint("topology_definition_id"),
        sa.UniqueConstraint(
            "root_assessment_version_id",
            name="uq_assessment_topology_definition_root_version",
        ),
    )
    op.create_index(
        "ix_assessment_topology_definition_application",
        "assessment_topology_definitions",
        ["application_id"],
    )
    op.create_index(
        "ix_assessment_topology_definition_structure_hash",
        "assessment_topology_definitions",
        ["structure_hash"],
    )

    op.create_table(
        "assessment_topology_nodes",
        sa.Column("topology_node_id", sa.String(length=96), nullable=False),
        sa.Column("topology_definition_id", sa.String(length=96), nullable=False),
        sa.Column("anchor_key", sa.String(length=192), nullable=False),
        sa.Column("anchor_type", sa.String(length=64), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["topology_definition_id"],
            ["assessment_topology_definitions.topology_definition_id"],
        ),
        sa.PrimaryKeyConstraint("topology_node_id"),
        sa.UniqueConstraint(
            "topology_definition_id",
            "anchor_key",
            name="uq_assessment_topology_node_key",
        ),
    )
    op.create_index(
        "ix_assessment_topology_node_definition_type",
        "assessment_topology_nodes",
        ["topology_definition_id", "anchor_type"],
    )

    op.create_table(
        "assessment_topology_edges",
        sa.Column("topology_edge_id", sa.String(length=96), nullable=False),
        sa.Column("topology_definition_id", sa.String(length=96), nullable=False),
        sa.Column("parent_node_id", sa.String(length=96), nullable=False),
        sa.Column("child_node_id", sa.String(length=96), nullable=False),
        sa.Column("relation_type", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("parent_node_id <> child_node_id", name="ck_assessment_topology_edge_not_self"),
        sa.ForeignKeyConstraint(
            ["topology_definition_id"],
            ["assessment_topology_definitions.topology_definition_id"],
        ),
        sa.ForeignKeyConstraint(["parent_node_id"], ["assessment_topology_nodes.topology_node_id"]),
        sa.ForeignKeyConstraint(["child_node_id"], ["assessment_topology_nodes.topology_node_id"]),
        sa.PrimaryKeyConstraint("topology_edge_id"),
        sa.UniqueConstraint(
            "topology_definition_id",
            "parent_node_id",
            "child_node_id",
            "relation_type",
            name="uq_assessment_topology_edge",
        ),
    )
    op.create_index("ix_assessment_topology_edge_child", "assessment_topology_edges", ["child_node_id"])

    op.create_table(
        "assessment_topology_node_references",
        sa.Column("topology_node_reference_id", sa.String(length=96), nullable=False),
        sa.Column("topology_node_id", sa.String(length=96), nullable=False),
        sa.Column("reference_role", sa.String(length=64), nullable=False),
        sa.Column("reference_kind", sa.String(length=64), nullable=False),
        sa.Column("reference_id", sa.String(length=192), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["topology_node_id"], ["assessment_topology_nodes.topology_node_id"]),
        sa.PrimaryKeyConstraint("topology_node_reference_id"),
        sa.UniqueConstraint(
            "topology_node_id",
            "reference_role",
            "reference_kind",
            "reference_id",
            name="uq_assessment_topology_node_reference",
        ),
    )
    op.create_index(
        "ix_assessment_topology_node_reference_lookup",
        "assessment_topology_node_references",
        ["reference_kind", "reference_id"],
    )

    # 2. 在既有 Snapshot 上增加新结构的关联。保留 nullable 是为了不破坏 088 已写入的数据。
    op.add_column(
        "assessment_topology_snapshots",
        sa.Column("topology_definition_id", sa.String(length=96), nullable=True),
    )
    op.add_column(
        "assessment_topology_snapshots",
        sa.Column("previous_topology_snapshot_id", sa.String(length=96), nullable=True),
    )
    op.add_column(
        "assessment_topology_snapshots",
        sa.Column(
            "state_schema_version",
            sa.String(length=64),
            nullable=False,
            server_default="assessment_topology_state_v1",
        ),
    )
    op.add_column(
        "assessment_topology_snapshots",
        sa.Column("state_hash", sa.String(length=64), nullable=True),
    )
    op.create_foreign_key(
        "fk_assessment_topology_snapshot_definition",
        "assessment_topology_snapshots",
        "assessment_topology_definitions",
        ["topology_definition_id"],
        ["topology_definition_id"],
    )
    op.create_foreign_key(
        "fk_assessment_topology_snapshot_previous",
        "assessment_topology_snapshots",
        "assessment_topology_snapshots",
        ["previous_topology_snapshot_id"],
        ["topology_snapshot_id"],
    )
    op.create_index(
        "ix_assessment_topology_snapshot_definition",
        "assessment_topology_snapshots",
        ["topology_definition_id"],
    )
    op.create_index(
        "ix_assessment_topology_snapshots_previous_topology_snapshot_id",
        "assessment_topology_snapshots",
        ["previous_topology_snapshot_id"],
    )
    op.create_index(
        "ix_assessment_topology_snapshots_state_hash",
        "assessment_topology_snapshots",
        ["state_hash"],
    )

    # 3. 每版状态独立保存；V2/V3 以后只能复制上一版并更新命中的既有节点。
    op.create_table(
        "assessment_topology_node_states",
        sa.Column("topology_node_state_id", sa.String(length=96), nullable=False),
        sa.Column("topology_snapshot_id", sa.String(length=96), nullable=False),
        sa.Column("topology_node_id", sa.String(length=96), nullable=False),
        sa.Column("score_value", sa.Float(), nullable=True),
        sa.Column("level_value", sa.Float(), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["topology_snapshot_id"],
            ["assessment_topology_snapshots.topology_snapshot_id"],
        ),
        sa.ForeignKeyConstraint(["topology_node_id"], ["assessment_topology_nodes.topology_node_id"]),
        sa.PrimaryKeyConstraint("topology_node_state_id"),
        sa.UniqueConstraint(
            "topology_snapshot_id",
            "topology_node_id",
            name="uq_assessment_topology_node_state",
        ),
    )
    op.create_index(
        "ix_assessment_topology_state_snapshot_score",
        "assessment_topology_node_states",
        ["topology_snapshot_id", "score_value"],
    )

    # 4. V2/V3 未来写入面评增量；V1 不会产生该表记录。
    op.create_table(
        "assessment_anchor_updates",
        sa.Column("assessment_anchor_update_id", sa.String(length=96), nullable=False),
        sa.Column("topology_snapshot_id", sa.String(length=96), nullable=False),
        sa.Column("topology_node_id", sa.String(length=96), nullable=False),
        sa.Column("source_interview_parse_result_id", sa.String(length=96), nullable=True),
        sa.Column("assertion_key", sa.String(length=128), nullable=False),
        sa.Column("judgement", sa.String(length=32), nullable=False),
        sa.Column("before_score", sa.Float(), nullable=True),
        sa.Column("after_score", sa.Float(), nullable=True),
        sa.Column("source_excerpt", sa.Text(), nullable=True),
        sa.Column("reason_text", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["topology_snapshot_id"],
            ["assessment_topology_snapshots.topology_snapshot_id"],
        ),
        sa.ForeignKeyConstraint(["topology_node_id"], ["assessment_topology_nodes.topology_node_id"]),
        sa.ForeignKeyConstraint(
            ["source_interview_parse_result_id"],
            ["interview_parse_results.parse_result_id"],
        ),
        sa.PrimaryKeyConstraint("assessment_anchor_update_id"),
    )
    op.create_index(
        "ix_assessment_anchor_update_snapshot_node",
        "assessment_anchor_updates",
        ["topology_snapshot_id", "topology_node_id"],
    )
    op.create_index(
        "ix_assessment_anchor_update_parse_result",
        "assessment_anchor_updates",
        ["source_interview_parse_result_id"],
    )


def downgrade() -> None:
    raise RuntimeError("评分拓扑规范化迁移不支持降级；请通过前向迁移修复。")