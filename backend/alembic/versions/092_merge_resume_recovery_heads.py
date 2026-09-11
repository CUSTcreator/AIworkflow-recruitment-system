"""Merge the resume self-recovery branch into the current schema line.

Revision ID: 092_merge_resume_recovery_heads
Revises: 091_resume_recovery_contract, 063_resume_submission_self_recovery

This migration changes no data or table shape. It records that both recovery
contracts must be present before later migrations can continue from one head.
"""
from __future__ import annotations


revision = "092_merge_resume_recovery_heads"
down_revision = (
    "091_resume_recovery_contract",
    "063_resume_submission_self_recovery",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
