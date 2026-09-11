"""add business permissions and HR department

Revision ID: 025_business_permissions
Revises: 024_application_deletion
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "025_business_permissions"
down_revision = "024_application_deletion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    user_columns = {column["name"] for column in inspector.get_columns("users")}
    if "business_scope" not in user_columns:
        op.add_column(
            "users",
            sa.Column(
                "business_scope",
                sa.String(32),
                nullable=False,
                server_default="department",
            ),
        )
        op.create_index("ix_users_business_scope", "users", ["business_scope"])

    tables = set(sa.inspect(bind).get_table_names())
    if "user_permission_overrides" not in tables:
        op.create_table(
            "user_permission_overrides",
            sa.Column("permission_override_id", sa.String(64), primary_key=True),
            sa.Column("user_id", sa.String(64), sa.ForeignKey("users.user_id"), nullable=False),
            sa.Column("permission_code", sa.String(96), nullable=False),
            sa.Column("effect", sa.String(16), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("user_id", "permission_code", name="uq_user_permission_override"),
        )
        op.create_index("ix_user_permission_overrides_user_id", "user_permission_overrides", ["user_id"])
        op.create_index("ix_user_permission_overrides_permission_code", "user_permission_overrides", ["permission_code"])

    departments = sa.table(
        "departments",
        sa.column("department_id", sa.String),
        sa.column("name", sa.String),
        sa.column("is_active", sa.Boolean),
    )
    existing_hr_department = bind.execute(
        sa.select(departments.c.department_id).where(
            departments.c.department_id == "DEPT_HR"
        )
    ).scalar_one_or_none()
    if existing_hr_department is None:
        op.bulk_insert(
            departments,
            [{"department_id": "DEPT_HR", "name": "人事部", "is_active": True}],
        )
    bind.execute(
        sa.text(
            "UPDATE users SET department_id = :department_id, "
            "business_scope = :business_scope WHERE role = :role"
        ),
        {
            "department_id": "DEPT_HR",
            "business_scope": "organization",
            "role": "hr",
        },
    )
    bind.execute(
        sa.text(
            "UPDATE users SET business_scope = :business_scope "
            "WHERE role <> :role AND (business_scope IS NULL OR business_scope = '')"
        ),
        {"business_scope": "department", "role": "hr"},
    )


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "user_permission_overrides" in tables:
        op.drop_table("user_permission_overrides")
    columns = {column["name"] for column in sa.inspect(bind).get_columns("users")}
    if "business_scope" in columns:
        op.drop_index("ix_users_business_scope", table_name="users")
        op.drop_column("users", "business_scope")
