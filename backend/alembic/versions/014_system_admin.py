"""add system administrator capabilities

Revision ID: 014_system_admin
Revises: 013_unique_department_name
"""

from __future__ import annotations

from datetime import datetime

from alembic import op
import sqlalchemy as sa


revision = "014_system_admin"
down_revision = "013_unique_department_name"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    user_columns = {item["name"] for item in inspector.get_columns("users")}
    user_indexes = {item["name"] for item in inspector.get_indexes("users")}
    if "is_system_admin" not in user_columns:
        op.add_column(
            "users",
            sa.Column(
                "is_system_admin",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )
    if "ix_users_is_system_admin" not in user_indexes:
        op.create_index("ix_users_is_system_admin", "users", ["is_system_admin"])

    department_columns = {
        item["name"] for item in inspector.get_columns("departments")
    }
    department_indexes = {
        item["name"] for item in inspector.get_indexes("departments")
    }
    if "is_active" not in department_columns:
        op.add_column(
            "departments",
            sa.Column(
                "is_active",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            ),
        )
    if "ix_departments_is_active" not in department_indexes:
        op.create_index("ix_departments_is_active", "departments", ["is_active"])

    job_columns = {item["name"] for item in inspector.get_columns("jobs")}
    job_indexes = {item["name"] for item in inspector.get_indexes("jobs")}
    if "status" not in job_columns:
        op.add_column(
            "jobs",
            sa.Column(
                "status",
                sa.String(32),
                nullable=False,
                server_default="open",
            ),
        )
    if "headcount" not in job_columns:
        op.add_column("jobs", sa.Column("headcount", sa.Integer(), nullable=True))
    if "hiring_manager_id" not in job_columns:
        op.add_column(
            "jobs",
            sa.Column("hiring_manager_id", sa.String(64), nullable=True),
        )
    foreign_keys = {
        item["name"] for item in inspector.get_foreign_keys("jobs")
    }
    if (
        bind.dialect.name != "sqlite"
        and "fk_jobs_hiring_manager_id_users" not in foreign_keys
    ):
        op.create_foreign_key(
            "fk_jobs_hiring_manager_id_users",
            "jobs",
            "users",
            ["hiring_manager_id"],
            ["user_id"],
        )
    if "ix_jobs_status" not in job_indexes:
        op.create_index("ix_jobs_status", "jobs", ["status"])
    if "ix_jobs_hiring_manager_id" not in job_indexes:
        op.create_index(
            "ix_jobs_hiring_manager_id",
            "jobs",
            ["hiring_manager_id"],
        )

    if not inspector.has_table("audit_events"):
        op.create_table(
            "audit_events",
            sa.Column("audit_event_id", sa.String(64), primary_key=True),
            sa.Column(
                "actor_user_id",
                sa.String(64),
                sa.ForeignKey("users.user_id"),
                nullable=True,
            ),
            sa.Column("actor_name", sa.String(128), nullable=False),
            sa.Column("action", sa.String(96), nullable=False),
            sa.Column("target_type", sa.String(64), nullable=False),
            sa.Column("target_id", sa.String(96), nullable=False),
            sa.Column("summary", sa.String(500), nullable=False),
            sa.Column("details", sa.JSON(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                default=datetime.utcnow,
            ),
        )
        op.create_index(
            "ix_audit_events_actor_user_id",
            "audit_events",
            ["actor_user_id"],
        )
        op.create_index("ix_audit_events_action", "audit_events", ["action"])
        op.create_index(
            "ix_audit_events_target_type",
            "audit_events",
            ["target_type"],
        )
        op.create_index(
            "ix_audit_events_target_id",
            "audit_events",
            ["target_id"],
        )
        op.create_index(
            "ix_audit_events_created_at",
            "audit_events",
            ["created_at"],
        )
        op.create_index(
            "ix_audit_events_target",
            "audit_events",
            ["target_type", "target_id"],
        )

    metadata = sa.MetaData()
    users = sa.Table("users", metadata, autoload_with=bind)
    jobs = sa.Table("jobs", metadata, autoload_with=bind)

    bind.execute(
        users.update()
        .where(users.c.user_id == "U_HR")
        .values(is_system_admin=True)
    )

    managers = list(
        bind.execute(
            sa.select(users.c.user_id, users.c.department_id)
            .where(
                users.c.role == "department_manager",
                users.c.is_active.is_(True),
            )
            .order_by(users.c.user_id)
        )
    )
    manager_by_department: dict[str, str] = {}
    for user_id, department_id in managers:
        if department_id and department_id not in manager_by_department:
            manager_by_department[department_id] = user_id

    for row in bind.execute(
        sa.select(jobs.c.job_id, jobs.c.department_id, jobs.c.payload)
    ).mappings():
        payload = dict(row["payload"] or {})
        payload_status = str(payload.get("status") or "active")
        status = "open" if payload_status == "active" else "closed"
        headcount = payload.get("headcount")
        if not isinstance(headcount, int):
            headcount = None
        bind.execute(
            jobs.update()
            .where(jobs.c.job_id == row["job_id"])
            .values(
                status=status,
                headcount=headcount,
                hiring_manager_id=manager_by_department.get(row["department_id"]),
            )
        )


def downgrade() -> None:
    op.drop_table("audit_events")
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.drop_index("ix_jobs_hiring_manager_id")
        batch_op.drop_index("ix_jobs_status")
        batch_op.drop_constraint("fk_jobs_hiring_manager_id_users", type_="foreignkey")
        batch_op.drop_column("hiring_manager_id")
        batch_op.drop_column("headcount")
        batch_op.drop_column("status")
    with op.batch_alter_table("departments") as batch_op:
        batch_op.drop_index("ix_departments_is_active")
        batch_op.drop_column("is_active")
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_index("ix_users_is_system_admin")
        batch_op.drop_column("is_system_admin")
