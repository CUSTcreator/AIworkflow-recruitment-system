"""Store the originating HTTP request identifier on workflow runs.

Revision ID: 041_workflow_request_correlation
Revises: 040_hard_screening_catalog
"""

from alembic import op
import sqlalchemy as sa


revision = "041_workflow_request_correlation"
down_revision = "040_hard_screening_catalog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("workflow_runs", sa.Column("request_id", sa.String(length=128), nullable=True))
    op.create_index("ix_workflow_runs_request_id", "workflow_runs", ["request_id"])


def downgrade() -> None:
    op.drop_index("ix_workflow_runs_request_id", table_name="workflow_runs")
    op.drop_column("workflow_runs", "request_id")
