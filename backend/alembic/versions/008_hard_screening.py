"""add hard screening policies and results

Revision ID: 008_hard_screening
Revises: 007_job_identity_and_versions
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "008_hard_screening"
down_revision = "007_job_identity_and_versions"
branch_labels = None
depends_on = None


def _json_type() -> sa.types.TypeEngine:
    return sa.JSON()


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "hard_screening_policies" not in tables:
        op.create_table(
            "hard_screening_policies",
            sa.Column("policy_id", sa.String(96), primary_key=True),
            sa.Column("job_id", sa.String(64), sa.ForeignKey("jobs.job_id"), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("rules_json", _json_type(), nullable=False),
            sa.Column("created_by", sa.String(64), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint(
                "job_id",
                "version",
                name="uq_hard_screening_policy_job_version",
            ),
        )
        op.create_index(
            "ix_hard_screening_policies_job_id",
            "hard_screening_policies",
            ["job_id"],
        )
    if "hard_screening_results" not in tables:
        op.create_table(
            "hard_screening_results",
            sa.Column("result_id", sa.String(96), primary_key=True),
            sa.Column(
                "application_id",
                sa.String(64),
                sa.ForeignKey("applications.application_id"),
                nullable=False,
            ),
            sa.Column(
                "policy_id",
                sa.String(96),
                sa.ForeignKey("hard_screening_policies.policy_id"),
                nullable=False,
            ),
            sa.Column(
                "workflow_run_id",
                sa.String(64),
                sa.ForeignKey("workflow_runs.workflow_run_id"),
                nullable=True,
            ),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("payload", _json_type(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint(
                "application_id",
                "policy_id",
                name="uq_hard_screening_result_application_policy",
            ),
        )
        op.create_index(
            "ix_hard_screening_results_application_id",
            "hard_screening_results",
            ["application_id"],
        )
        op.create_index(
            "ix_hard_screening_results_policy_id",
            "hard_screening_results",
            ["policy_id"],
        )
        op.create_index(
            "ix_hard_screening_results_status",
            "hard_screening_results",
            ["status"],
        )


def downgrade() -> None:
    op.drop_index("ix_hard_screening_results_status", table_name="hard_screening_results")
    op.drop_index("ix_hard_screening_results_policy_id", table_name="hard_screening_results")
    op.drop_index("ix_hard_screening_results_application_id", table_name="hard_screening_results")
    op.drop_table("hard_screening_results")
    op.drop_index("ix_hard_screening_policies_job_id", table_name="hard_screening_policies")
    op.drop_table("hard_screening_policies")
