"""add durable workflow execution timeline

Revision ID: 060_workflow_execution_events
Revises: 059_workflow_step_poll_policy
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "060_workflow_execution_events"
down_revision = "059_workflow_step_poll_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workflow_execution_events",
        sa.Column("execution_event_id", sa.String(length=64), primary_key=True),
        sa.Column("workflow_run_id", sa.String(length=64), sa.ForeignKey("workflow_runs.workflow_run_id"), nullable=False),
        sa.Column("application_id", sa.String(length=64), nullable=True),
        sa.Column("candidate_id", sa.String(length=64), nullable=True),
        sa.Column("resume_submission_id", sa.String(length=96), nullable=True),
        sa.Column("step_name", sa.String(length=96), nullable=True),
        sa.Column("event_type", sa.String(length=96), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False, server_default="info"),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("external_request_id", sa.String(length=128), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=True),
        sa.Column("poll_count", sa.Integer(), nullable=True),
        sa.Column("error_category", sa.String(length=48), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("public_message", sa.String(length=500), nullable=False),
        sa.Column("diagnostic_fields", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_workflow_execution_events_run_time", "workflow_execution_events", ["workflow_run_id", "occurred_at"])
    op.create_index("ix_workflow_execution_events_application_time", "workflow_execution_events", ["application_id", "occurred_at"])
    op.create_index("ix_workflow_execution_events_candidate_time", "workflow_execution_events", ["candidate_id", "occurred_at"])


def downgrade() -> None:
    op.drop_table("workflow_execution_events")