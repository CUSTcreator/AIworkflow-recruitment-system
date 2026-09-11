"""Merge login-audit and business-time migration branches.

Revision ID: 030_merge_audit_times
Revises: 028_remove_login_audit, 029_simplify_times
"""

from __future__ import annotations


revision = "030_merge_audit_times"
down_revision = ("028_remove_login_audit", "029_simplify_times")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
