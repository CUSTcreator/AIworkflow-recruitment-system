"""Persist confirmation-page requirement classification on JobDraft.

The field is a named contract: ``items`` is reused by the profile workflow and
``hardScreeningRules`` is projected to the confirmation-page DTO.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "097_job_draft_requirement_classification"
down_revision = "096_permission_catalog_cleanup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "job_drafts",
        sa.Column(
            "requirement_classification_json",
            sa.JSON().with_variant(JSONB(), "postgresql"),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("job_drafts", "requirement_classification_json")
