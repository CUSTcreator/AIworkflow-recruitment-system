"""Persist Application recovery facts from profile release through V1 publish.

Revision ID: 094_application_recovery
Revises: 093_job_recovery_contract
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "094_application_recovery"
down_revision = "093_job_recovery_contract"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("applications", sa.Column("recovery_code", sa.String(length=64), nullable=True))
    op.add_column(
        "applications",
        sa.Column(
            "recovery_context_json",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.create_index("ix_applications_recovery_code", "applications", ["recovery_code"])
    op.alter_column("applications", "recovery_context_json", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_applications_recovery_code", table_name="applications")
    op.drop_column("applications", "recovery_context_json")
    op.drop_column("applications", "recovery_code")
