"""track per-user import record reads

Revision ID: 021_import_record_reads
Revises: 020_department_recruiter
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "021_import_record_reads"
down_revision = "020_department_recruiter"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "import_record_read_cursors" in tables or "notification_read_cursors" in tables:
        return
    op.create_table(
        "import_record_read_cursors",
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("import_type", sa.String(16), nullable=False),
        sa.Column("last_read_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("user_id", "import_type"),
    )


def downgrade() -> None:
    op.drop_table("import_record_read_cursors")
