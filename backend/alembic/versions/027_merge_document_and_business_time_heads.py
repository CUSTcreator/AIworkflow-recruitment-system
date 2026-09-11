"""Merge document metadata and recruitment business-time migrations.

Revision ID: 027_merge_026_heads
Revises: 026_application_documents, 026_recruitment_times
"""

from __future__ import annotations


revision = "027_merge_026_heads"
down_revision = (
    "026_application_documents",
    "026_recruitment_times",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
