"""add two-role account authentication fields

Revision ID: 002_two_role_auth
Revises: 001_initial_schema
"""

import sqlalchemy as sa
from alembic import op


revision = "002_two_role_auth"
down_revision = "001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {item["name"] for item in inspector.get_columns("users")}
    indexes = {item["name"] for item in inspector.get_indexes("users")}
    with op.batch_alter_table("users") as batch_op:
        if "username" not in columns:
            batch_op.add_column(sa.Column("username", sa.String(length=64), nullable=True))
        if "password_hash" not in columns:
            batch_op.add_column(sa.Column("password_hash", sa.String(length=256), nullable=True))
        if "is_active" not in columns:
            batch_op.add_column(sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()))
        if "must_change_password" not in columns:
            batch_op.add_column(sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.false()))
        if "last_login_at" not in columns:
            batch_op.add_column(sa.Column("last_login_at", sa.DateTime(), nullable=True))
        if "ix_users_username" not in indexes:
            batch_op.create_index("ix_users_username", ["username"], unique=True)


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_index("ix_users_username")
        batch_op.drop_column("last_login_at")
        batch_op.drop_column("must_change_password")
        batch_op.drop_column("is_active")
        batch_op.drop_column("password_hash")
        batch_op.drop_column("username")
