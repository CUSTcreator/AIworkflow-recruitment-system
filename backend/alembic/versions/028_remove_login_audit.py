"""Remove successful login events from operation audit.

Revision ID: 028_remove_login_audit
Revises: 027_merge_026_heads
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "028_remove_login_audit"
down_revision = "027_merge_026_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    audit_events = sa.table("audit_events", sa.column("action", sa.String()))
    op.execute(audit_events.delete().where(audit_events.c.action == "auth.login"))


def downgrade() -> None:
    pass
