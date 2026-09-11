"""turn workflow runs into a durable queue

Revision ID: 005_workflow_run_queue
Revises: 004_merge_and_ranking_ties
"""

from alembic import op
import sqlalchemy as sa

from backend.app.models.entities import JsonType


revision = "005_workflow_run_queue"
down_revision = "004_merge_and_ranking_ties"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {item["name"] for item in inspector.get_columns("workflow_runs")}
    indexes = {item["name"] for item in inspector.get_indexes("workflow_runs")}
    with op.batch_alter_table("workflow_runs") as batch_op:
        batch_op.alter_column("status", server_default="pending")
        additions = {
            "input_json": sa.Column("input_json", JsonType, nullable=False, server_default="{}"),
            "available_at": sa.Column("available_at", sa.DateTime(), nullable=True),
            "attempt_count": sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
            "max_attempts": sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
            "lease_owner": sa.Column("lease_owner", sa.String(128), nullable=True),
            "lease_expires_at": sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
            "heartbeat_at": sa.Column("heartbeat_at", sa.DateTime(), nullable=True),
            "updated_at": sa.Column("updated_at", sa.DateTime(), nullable=True),
        }
        for name, column in additions.items():
            if name not in columns:
                batch_op.add_column(column)
        if "ix_workflow_runs_queue" not in indexes:
            batch_op.create_index("ix_workflow_runs_queue", ["status", "available_at"], unique=False)
        if "ix_workflow_runs_application_type" not in indexes:
            batch_op.create_index(
                "ix_workflow_runs_application_type",
                ["application_id", "workflow_type", "started_at"],
                unique=False,
            )
    op.execute("UPDATE workflow_runs SET updated_at = started_at WHERE updated_at IS NULL")


def downgrade() -> None:
    with op.batch_alter_table("workflow_runs") as batch_op:
        batch_op.drop_index("ix_workflow_runs_application_type")
        batch_op.drop_index("ix_workflow_runs_queue")
        batch_op.drop_column("updated_at")
        batch_op.drop_column("heartbeat_at")
        batch_op.drop_column("lease_expires_at")
        batch_op.drop_column("lease_owner")
        batch_op.drop_column("max_attempts")
        batch_op.drop_column("attempt_count")
        batch_op.drop_column("available_at")
        batch_op.drop_column("input_json")
        batch_op.alter_column("status", server_default="completed")
