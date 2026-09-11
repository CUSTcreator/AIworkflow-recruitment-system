"""Add duplicate-intake resolution state.

Revision ID: 039_duplicate_intake_resolution
Revises: 038_structure_soft_delete
"""

from alembic import op
import sqlalchemy as sa


revision = "039_duplicate_intake_resolution"
down_revision = "038_structure_soft_delete"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE applications SET status = 'closed_rejected' WHERE status = 'talent_pool'")
    with op.batch_alter_table("job_drafts") as batch:
        batch.add_column(sa.Column("duplicate_status", sa.String(length=32), nullable=False, server_default="new_job"))
        batch.add_column(sa.Column("existing_job_id", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("duplicate_group_id", sa.String(length=96), nullable=True))
        batch.add_column(sa.Column("resolution", sa.String(length=24), nullable=True))
        batch.create_index("ix_job_drafts_duplicate_status", ["duplicate_status"])
        batch.create_index("ix_job_drafts_existing_job_id", ["existing_job_id"])
        batch.create_index("ix_job_drafts_duplicate_group_id", ["duplicate_group_id"])
        batch.create_foreign_key("fk_job_drafts_existing_job", "jobs", ["existing_job_id"], ["job_id"])
    with op.batch_alter_table("applications") as batch:
        batch.drop_index("uq_applications_active_candidate_job")
        batch.create_index(
            "uq_applications_active_candidate_job",
            ["candidate_id", "job_id"],
            unique=True,
            postgresql_where=sa.text("deleted_at IS NULL AND status NOT IN ('resume_replaced', 'offer_process', 'closed_rejected', 'closed_cancelled')"),
            sqlite_where=sa.text("deleted_at IS NULL AND status NOT IN ('resume_replaced', 'offer_process', 'closed_rejected', 'closed_cancelled')"),
        )


def downgrade() -> None:
    with op.batch_alter_table("applications") as batch:
        batch.drop_index("uq_applications_active_candidate_job")
        batch.create_index(
            "uq_applications_active_candidate_job", ["candidate_id", "job_id"], unique=True,
            postgresql_where=sa.text("deleted_at IS NULL"),
            sqlite_where=sa.text("deleted_at IS NULL"),
        )
    with op.batch_alter_table("job_drafts") as batch:
        batch.drop_constraint("fk_job_drafts_existing_job", type_="foreignkey")
        batch.drop_index("ix_job_drafts_duplicate_group_id")
        batch.drop_index("ix_job_drafts_existing_job_id")
        batch.drop_index("ix_job_drafts_duplicate_status")
        batch.drop_column("resolution")
        batch.drop_column("duplicate_group_id")
        batch.drop_column("existing_job_id")
        batch.drop_column("duplicate_status")
