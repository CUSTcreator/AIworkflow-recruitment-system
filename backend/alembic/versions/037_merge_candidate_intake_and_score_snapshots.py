"""Merge candidate intake routing and score snapshot migration heads."""

from __future__ import annotations

revision = "037_merge_intake_snapshots"
down_revision = ("035_candidate_intake_routing", "036_version_score_snapshots")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
