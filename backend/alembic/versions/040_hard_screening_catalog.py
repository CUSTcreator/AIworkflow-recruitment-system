"""Add system hard-screening criterion catalog.

Revision ID: 040_hard_screening_catalog
Revises: 039_duplicate_intake_resolution
"""

from alembic import op
import sqlalchemy as sa


revision = "040_hard_screening_catalog"
down_revision = "039_duplicate_intake_resolution"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hard_screening_criteria",
        sa.Column("criterion_id", sa.String(length=96), primary_key=True),
        sa.Column("code", sa.String(length=96), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("value_mode", sa.String(length=16), nullable=False),
        sa.Column("allowed_values_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("evaluation_binding", sa.String(length=96), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_builtin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("code", name="uq_hard_screening_criterion_code"),
    )
    op.create_index("ix_hard_screening_criteria_code", "hard_screening_criteria", ["code"])
    op.create_index("ix_hard_screening_criteria_deleted_at", "hard_screening_criteria", ["deleted_at"])
    op.create_index("ix_hard_screening_criteria_enabled_order", "hard_screening_criteria", ["enabled", "sort_order"])


def downgrade() -> None:
    op.drop_index("ix_hard_screening_criteria_enabled_order", table_name="hard_screening_criteria")
    op.drop_index("ix_hard_screening_criteria_deleted_at", table_name="hard_screening_criteria")
    op.drop_index("ix_hard_screening_criteria_code", table_name="hard_screening_criteria")
    op.drop_table("hard_screening_criteria")