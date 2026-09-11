"""persist immutable interview records

Revision ID: 006_interview_records
Revises: 005_workflow_run_queue
"""

from alembic import op
import sqlalchemy as sa

from backend.app.models.entities import JsonType


revision = "006_interview_records"
down_revision = "005_workflow_run_queue"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "interview_records" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "interview_records",
        sa.Column("record_id", sa.String(96), primary_key=True),
        sa.Column(
            "application_id",
            sa.String(64),
            sa.ForeignKey("applications.application_id"),
            nullable=False,
        ),
        sa.Column(
            "interview_id",
            sa.String(64),
            sa.ForeignKey("interviews.interview_id"),
            nullable=True,
        ),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("record_type", sa.String(32), nullable=False),
        sa.Column("question_id", sa.String(96), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("payload", JsonType, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_interview_records_application_id",
        "interview_records",
        ["application_id"],
    )
    op.create_index("ix_interview_records_stage", "interview_records", ["stage"])


def downgrade() -> None:
    op.drop_index("ix_interview_records_stage", table_name="interview_records")
    op.drop_index(
        "ix_interview_records_application_id", table_name="interview_records"
    )
    op.drop_table("interview_records")
