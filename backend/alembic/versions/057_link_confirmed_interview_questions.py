"""link confirmed interview guides and questions to plan versions

Revision ID: 057_interview_q_links
Revises: 056_merge_step_runtime
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "057_interview_q_links"
down_revision = "056_merge_step_runtime"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """新增正式题单的显式版本关联，不删除或猜测回填历史 payload。"""
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    tables = set(inspector.get_table_names())
    if "interview_guides" in tables:
        columns = {column["name"] for column in inspector.get_columns("interview_guides")}
        with op.batch_alter_table("interview_guides") as batch:
            if "guide_id" not in columns:
                batch.add_column(sa.Column("guide_id", sa.String(length=96), nullable=True))
            if "plan_version_id" not in columns:
                batch.add_column(sa.Column("plan_version_id", sa.String(length=96), nullable=True))
                batch.create_foreign_key(
                    "fk_interview_guides_plan_version",
                    "first_interview_plan_versions",
                    ["plan_version_id"],
                    ["plan_version_id"],
                )
            batch.create_index("ix_interview_guides_plan_version", ["plan_version_id"])
            batch.create_unique_constraint(
                "uq_interview_guide_application_guide", ["application_id", "guide_id"]
            )
    if "interview_questions" in tables:
        columns = {column["name"] for column in inspector.get_columns("interview_questions")}
        with op.batch_alter_table("interview_questions") as batch:
            if "guide_id" not in columns:
                batch.add_column(sa.Column("guide_id", sa.String(length=96), nullable=True))
            if "plan_version_id" not in columns:
                batch.add_column(sa.Column("plan_version_id", sa.String(length=96), nullable=True))
                batch.create_foreign_key(
                    "fk_interview_questions_plan_version",
                    "first_interview_plan_versions",
                    ["plan_version_id"],
                    ["plan_version_id"],
                )
            batch.create_index(
                "ix_interview_questions_plan_guide",
                ["application_id", "plan_version_id", "guide_id"],
            )


def downgrade() -> None:
    with op.batch_alter_table("interview_questions") as batch:
        batch.drop_index("ix_interview_questions_plan_guide")
        batch.drop_constraint("fk_interview_questions_plan_version", type_="foreignkey")
        batch.drop_column("plan_version_id")
        batch.drop_column("guide_id")
    with op.batch_alter_table("interview_guides") as batch:
        batch.drop_constraint("uq_interview_guide_application_guide", type_="unique")
        batch.drop_index("ix_interview_guides_plan_version")
        batch.drop_constraint("fk_interview_guides_plan_version", type_="foreignkey")
        batch.drop_column("plan_version_id")
        batch.drop_column("guide_id")