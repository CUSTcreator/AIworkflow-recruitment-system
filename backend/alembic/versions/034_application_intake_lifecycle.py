"""Create applications at resume intake time.

Revision ID: 034_application_intake_lifecycle
Revises: 033_interview_guide_templates
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "034_application_intake_lifecycle"
down_revision = "033_interview_guide_templates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("applications") as batch:
        batch.alter_column("candidate_id", existing_type=sa.String(length=64), nullable=True)
        batch.create_index("ix_applications_candidate_id", ["candidate_id"])
    with op.batch_alter_table("workflow_runs") as batch:
        batch.add_column(sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()))
        batch.create_index("ix_workflow_runs_application_status_updated", ["application_id", "status", "updated_at"])
    with op.batch_alter_table("resume_submissions") as batch:
        batch.create_index("ix_resume_submissions_application_status", ["application_id", "status"])
    with op.batch_alter_table("tasks") as batch:
        batch.create_index("ix_tasks_assignee_status_due", ["assignee_user_id", "status", "due_at"])


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.drop_index("ix_tasks_assignee_status_due")
    with op.batch_alter_table("resume_submissions") as batch:
        batch.drop_index("ix_resume_submissions_application_status")
    with op.batch_alter_table("workflow_runs") as batch:
        batch.drop_index("ix_workflow_runs_application_status_updated")
        batch.drop_column("created_at")
    with op.batch_alter_table("applications") as batch:
        batch.drop_index("ix_applications_candidate_id")
        batch.alter_column("candidate_id", existing_type=sa.String(length=64), nullable=False)
