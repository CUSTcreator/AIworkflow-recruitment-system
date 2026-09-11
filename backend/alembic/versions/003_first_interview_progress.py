"""add first interview progress drafts

Revision ID: 003_first_interview_progress
Revises: 002_two_role_auth
"""

import sqlalchemy as sa
from alembic import op


revision = "003_first_interview_progress"
down_revision = "002_two_role_auth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_name = "first_interview_progress_drafts"
    if table_name not in inspector.get_table_names():
        op.create_table(
            table_name,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("application_id", sa.String(length=64), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index(
            "ix_first_interview_progress_drafts_application_id",
            table_name,
            ["application_id"],
        )


def downgrade() -> None:
    op.drop_index("ix_first_interview_progress_drafts_application_id", table_name="first_interview_progress_drafts")
    op.drop_table("first_interview_progress_drafts")
