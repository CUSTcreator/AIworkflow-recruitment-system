"""Drop legacy JSON bags from the resume intake aggregate.

Revision ID: 080_drop_resume_intake_legacy_json
Revises: 079_reconcile_profile_json_schema
Create Date: 2026-08-26

Resume intake now persists only named contracts: ResumeSubmission owns parse,
structure, review and routing fields; CandidateProfile owns basic identity; and
ResumeProfileRecord.profile_json owns the immutable structured resume.
"""
from __future__ import annotations

from alembic import op

revision = "080_drop_resume_intake_legacy_json"
down_revision = "079_reconcile_profile_json_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Remove obsolete columns after all production readers have been migrated."""
    op.drop_column("resume_submissions", "parse_metadata")
    op.drop_column("resume_submissions", "payload")
    op.drop_column("candidates", "payload")
    op.drop_column("resume_profiles", "payload")


def downgrade() -> None:
    """Schema-only rollback; deleted historical JSON content is intentionally not restored."""
    import sqlalchemy as sa

    op.add_column("resume_profiles", sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    op.add_column("candidates", sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    op.add_column("resume_submissions", sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    op.add_column("resume_submissions", sa.Column("parse_metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))