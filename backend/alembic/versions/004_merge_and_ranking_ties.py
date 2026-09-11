"""merge assessment branch and allow tied university ranks

Revision ID: 004_merge_and_ranking_ties
Revises: 002_assessment_profiles, 003_first_interview_progress
"""

from alembic import op
import sqlalchemy as sa


revision = "004_merge_and_ranking_ties"
down_revision = ("002_assessment_profiles", "003_first_interview_progress")
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    constraints = {
        item["name"]
        for item in inspector.get_unique_constraints("university_ranking_entries")
    }
    indexes = {
        item["name"]
        for item in inspector.get_indexes("university_ranking_entries")
    }
    with op.batch_alter_table("university_ranking_entries") as batch_op:
        if "uq_university_ranking_dataset_rank" in constraints:
            batch_op.drop_constraint("uq_university_ranking_dataset_rank", type_="unique")
        if "ix_university_ranking_dataset_rank" not in indexes:
            batch_op.create_index(
                "ix_university_ranking_dataset_rank",
                ["dataset_version", "rank"],
                unique=False,
            )


def downgrade() -> None:
    with op.batch_alter_table("university_ranking_entries") as batch_op:
        batch_op.drop_index("ix_university_ranking_dataset_rank")
        batch_op.create_unique_constraint(
            "uq_university_ranking_dataset_rank",
            ["dataset_version", "rank"],
        )
