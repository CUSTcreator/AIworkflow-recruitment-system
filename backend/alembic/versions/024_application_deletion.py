"""add application soft deletion

Revision ID: 024_application_deletion
Revises: 023_workspace_documents
"""

from alembic import op
import sqlalchemy as sa


revision = "024_application_deletion"
down_revision = "023_workspace_documents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {item["name"] for item in inspector.get_columns("applications")}
    indexes = {item["name"] for item in inspector.get_indexes("applications")}
    unique_constraints = {
        item["name"] for item in inspector.get_unique_constraints("applications")
    }
    with op.batch_alter_table("applications") as batch_op:
        if "deleted_at" not in columns:
            batch_op.add_column(sa.Column("deleted_at", sa.DateTime(), nullable=True))
        if "deleted_by_user_id" not in columns:
            batch_op.add_column(sa.Column("deleted_by_user_id", sa.String(64), nullable=True))
            batch_op.create_foreign_key(
                "fk_applications_deleted_by_user_id",
                "users",
                ["deleted_by_user_id"],
                ["user_id"],
            )
        if "uq_application_candidate_requisition" in unique_constraints:
            batch_op.drop_constraint(
                "uq_application_candidate_requisition",
                type_="unique",
            )

    inspector = sa.inspect(op.get_bind())
    indexes = {item["name"] for item in inspector.get_indexes("applications")}
    if "ix_applications_deleted_at" not in indexes:
        op.create_index("ix_applications_deleted_at", "applications", ["deleted_at"])
    if "ix_applications_deleted_by_user_id" not in indexes:
        op.create_index(
            "ix_applications_deleted_by_user_id",
            "applications",
            ["deleted_by_user_id"],
        )
    if "uq_applications_active_candidate_job" not in indexes:
        op.create_index(
            "uq_applications_active_candidate_job",
            "applications",
            ["candidate_id", "job_id"],
            unique=True,
            postgresql_where=sa.text("deleted_at IS NULL"),
            sqlite_where=sa.text("deleted_at IS NULL"),
        )


def downgrade() -> None:
    op.drop_index(
        "uq_applications_active_candidate_job",
        table_name="applications",
    )
    op.drop_index("ix_applications_deleted_by_user_id", table_name="applications")
    op.drop_index("ix_applications_deleted_at", table_name="applications")
    with op.batch_alter_table("applications") as batch_op:
        batch_op.drop_constraint(
            "fk_applications_deleted_by_user_id",
            type_="foreignkey",
        )
        batch_op.drop_column("deleted_by_user_id")
        batch_op.drop_column("deleted_at")
        batch_op.create_unique_constraint(
            "uq_application_candidate_requisition",
            ["candidate_id", "job_id"],
        )
