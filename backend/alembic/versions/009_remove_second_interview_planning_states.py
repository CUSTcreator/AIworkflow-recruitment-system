"""remove obsolete second interview planning states

Revision ID: 009_remove_second_planning
Revises: 008_hard_screening
"""

from __future__ import annotations

from alembic import op


revision = "009_remove_second_planning"
down_revision = "008_hard_screening"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE applications
        SET status = 'second_interview_in_progress'
        WHERE status IN ('second_interview_planning', 'second_interview_scheduled')
        """
    )
    op.execute(
        """
        UPDATE workflow_runs
        SET status = 'failed',
            error_message = 'workflow_removed:second_interview_planning'
        WHERE workflow_type = 'second_interview_planning_workflow'
          AND status IN ('queued', 'running')
        """
    )


def downgrade() -> None:
    pass
