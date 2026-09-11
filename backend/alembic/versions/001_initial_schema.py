"""initial schema

Revision ID: 001_initial_schema
Revises:
Create Date: 2026-07-09
"""

from alembic import op

from backend.app.db.session import Base
from backend.app.models import entities  # noqa: F401

revision = "001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
