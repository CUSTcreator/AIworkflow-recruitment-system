"""add independent interview targets

Revision ID: 035_interview_targets
Revises: 034_application_intake_lifecycle
"""

from alembic import op
import sqlalchemy as sa

from backend.app.models.entities import JsonType


revision = "035_interview_targets"
down_revision = "034_application_intake_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "interview_targets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("application_id", sa.String(length=64), nullable=True),
        sa.Column("payload", JsonType, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["application_id"], ["applications.application_id"]
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_interview_targets_application_id"),
        "interview_targets",
        ["application_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_interview_targets_application_id"),
        table_name="interview_targets",
    )
    op.drop_table("interview_targets")
