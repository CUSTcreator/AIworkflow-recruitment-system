"""generalize import read cursors for navigation notifications

Revision ID: 022_notification_cursors
Revises: 021_import_record_reads
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "022_notification_cursors"
down_revision = "021_import_record_reads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "notification_read_cursors" in tables:
        return
    if "import_record_read_cursors" not in tables:
        return
    op.rename_table("import_record_read_cursors", "notification_read_cursors")
    with op.batch_alter_table("notification_read_cursors") as batch:
        batch.alter_column(
            "import_type",
            new_column_name="channel",
            existing_type=sa.String(16),
            type_=sa.String(32),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("notification_read_cursors") as batch:
        batch.alter_column(
            "channel",
            new_column_name="import_type",
            existing_type=sa.String(32),
            type_=sa.String(16),
            existing_nullable=False,
        )
    op.rename_table("notification_read_cursors", "import_record_read_cursors")
