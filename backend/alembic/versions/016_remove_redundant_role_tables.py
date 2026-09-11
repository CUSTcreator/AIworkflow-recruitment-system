"""remove redundant role mapping tables

Revision ID: 016_remove_role_tables
Revises: 015_job_positions
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "016_remove_role_tables"
down_revision = "015_job_positions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("user_roles"):
        op.drop_table("user_roles")
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("roles"):
        op.drop_table("roles")


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("roles"):
        op.create_table(
            "roles",
            sa.Column("role_id", sa.String(64), primary_key=True),
            sa.Column("name", sa.String(64), nullable=False, unique=True),
        )
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("user_roles"):
        op.create_table(
            "user_roles",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "user_id",
                sa.String(64),
                sa.ForeignKey("users.user_id"),
                nullable=False,
            ),
            sa.Column(
                "role_id",
                sa.String(64),
                sa.ForeignKey("roles.role_id"),
                nullable=False,
            ),
            sa.UniqueConstraint(
                "user_id",
                "role_id",
                name="uq_user_role",
            ),
        )
