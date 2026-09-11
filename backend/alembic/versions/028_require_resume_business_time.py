"""require resume business submission time

Revision ID: 028_require_resume_time
Revises: 027_merge_026_heads
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "028_require_resume_time"
down_revision = "027_merge_026_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE resume_submissions "
        "SET business_submitted_at = created_at "
        "WHERE business_submitted_at IS NULL"
    )
    with op.batch_alter_table("resume_submissions") as batch:
        batch.alter_column(
            "business_submitted_at",
            existing_type=sa.DateTime(),
            nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("resume_submissions") as batch:
        batch.alter_column(
            "business_submitted_at",
            existing_type=sa.DateTime(),
            nullable=True,
        )
