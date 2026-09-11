"""enforce post interview assessment contracts

Revision ID: 052_post_interview_contracts
Revises: 051_first_interview_plans
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "052_post_interview_contracts"
down_revision = "051_first_interview_plans"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """只在现有数据已合同时添加约束；不擅自修改历史核验状态。"""
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    tables = set(inspector.get_table_names())
    if "interview_targets" in tables:
        invalid = connection.execute(sa.text(
            "SELECT COUNT(*) FROM interview_targets "
            "WHERE status IS NULL OR status NOT IN ('open', 'resolved')"
        )).scalar_one()
        if invalid:
            raise RuntimeError(f"interview_target_status_cleanup_required:{invalid}")
        with op.batch_alter_table("interview_targets") as batch:
            batch.create_check_constraint("ck_interview_target_status", "status IN ('open', 'resolved')")
    if "application_assessment_versions" in tables:
        invalid = connection.execute(sa.text(
            "SELECT COUNT(*) FROM application_assessment_versions "
            "WHERE stage NOT IN ('screening', 'after_first_interview', 'after_second_interview')"
        )).scalar_one()
        if invalid:
            raise RuntimeError(f"assessment_version_stage_cleanup_required:{invalid}")
        with op.batch_alter_table("application_assessment_versions") as batch:
            batch.create_check_constraint("ck_assessment_version_stage", "stage IN ('screening', 'after_first_interview', 'after_second_interview')")


def downgrade() -> None:
    with op.batch_alter_table("application_assessment_versions") as batch:
        batch.drop_constraint("ck_assessment_version_stage", type_="check")
    with op.batch_alter_table("interview_targets") as batch:
        batch.drop_constraint("ck_interview_target_status", type_="check")