"""correlate audit events with requests and workflows

Revision ID: 061_audit_event_correlation
Revises: 060_workflow_execution_events
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "061_audit_event_correlation"
down_revision = "060_workflow_execution_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("audit_events") as batch:
        batch.add_column(sa.Column("request_id", sa.String(length=128), nullable=True))
        batch.add_column(sa.Column("workflow_run_id", sa.String(length=64), nullable=True))
        batch.create_index("ix_audit_events_request", ["request_id", "created_at"])
        batch.create_index("ix_audit_events_workflow", ["workflow_run_id", "created_at"])


def downgrade() -> None:
    with op.batch_alter_table("audit_events") as batch:
        batch.drop_index("ix_audit_events_workflow")
        batch.drop_index("ix_audit_events_request")
        batch.drop_column("workflow_run_id")
        batch.drop_column("request_id")