"""初筛评估能力图拓扑快照。

Revision ID: 088_assessment_topology_snapshots
Revises: 087_activity_outcome_contract
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "088_assessment_topology_snapshots"
down_revision = "087_activity_outcome_contract"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assessment_topology_snapshots",
        sa.Column("topology_snapshot_id", sa.String(length=96), nullable=False),
        sa.Column("assessment_version_id", sa.String(length=96), nullable=False),
        sa.Column("application_id", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.String(length=64), nullable=False, server_default="assessment_topology_snapshot_v1"),
        sa.Column("source_core_hash", sa.String(length=64), nullable=False),
        sa.Column("topology_hash", sa.String(length=64), nullable=False),
        sa.Column("topology_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["assessment_version_id"], ["application_assessment_versions.assessment_version_id"]),
        sa.ForeignKeyConstraint(["application_id"], ["applications.application_id"]),
        sa.PrimaryKeyConstraint("topology_snapshot_id"),
        sa.UniqueConstraint("assessment_version_id", name="uq_assessment_topology_snapshot_version"),

    )
    op.create_index("ix_assessment_topology_snapshot_application", "assessment_topology_snapshots", ["application_id"])
    op.create_index("ix_assessment_topology_snapshots_assessment_version_id", "assessment_topology_snapshots", ["assessment_version_id"])
    op.create_index("ix_assessment_topology_snapshots_topology_hash", "assessment_topology_snapshots", ["topology_hash"])


def downgrade() -> None:
    raise RuntimeError("开发期评分拓扑快照迁移不支持降级")