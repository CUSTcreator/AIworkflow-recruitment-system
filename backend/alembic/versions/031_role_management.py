"""Add configurable business roles.

Revision ID: 031_role_management
Revises: 030_merge_audit_times
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "031_role_management"
down_revision = "030_merge_audit_times"
branch_labels = None
depends_on = None


DEFAULTS = {
    "hr": ("HR", "organization", ["resume.upload", "job_document.upload", "job_document.confirm", "job.edit", "job.delete", "hard_screening.manage", "screening.run", "candidate.view", "candidate.advance", "candidate.reject", "application.delete", "candidate_document.upload", "candidate_document.manage", "second_interview.manage", "final_decision.manage"]),
    "department_manager": ("部门主管", "department", ["candidate.view", "candidate.department.view", "job.hiring_manager.assign"]),
    "department_recruiter": ("部门招聘人员", "department", ["resume.upload", "candidate.view", "candidate.advance", "candidate.reject", "first_interview.manage", "candidate_document.upload"]),
}


def upgrade() -> None:
    role_table = op.create_table(
        "role_definitions",
        sa.Column("role_id", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False, unique=True),
        sa.Column("base_role", sa.String(length=64), nullable=False),
        sa.Column("business_scope", sa.String(length=32), nullable=False),
        sa.Column("permissions", sa.JSON(), nullable=False),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_role_definitions_base_role", "role_definitions", ["base_role"])
    op.create_index("ix_role_definitions_is_active", "role_definitions", ["is_active"])
    op.bulk_insert(role_table, [
        {"role_id": role_id, "name": name, "base_role": role_id, "business_scope": scope,
         "permissions": {code: True for code in permissions}, "is_system": True, "is_active": True}
        for role_id, (name, scope, permissions) in DEFAULTS.items()
    ])
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("role_definition_id", sa.String(length=64), nullable=True))
        batch.create_foreign_key("fk_users_role_definition_id", "role_definitions", ["role_definition_id"], ["role_id"])
        batch.create_index("ix_users_role_definition_id", ["role_definition_id"])
    op.execute(sa.text("UPDATE users SET role_definition_id = role WHERE role IN ('hr', 'department_manager', 'department_recruiter')"))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_index("ix_users_role_definition_id")
        batch.drop_constraint("fk_users_role_definition_id", type_="foreignkey")
        batch.drop_column("role_definition_id")
    op.drop_index("ix_role_definitions_is_active", table_name="role_definitions")
    op.drop_index("ix_role_definitions_base_role", table_name="role_definitions")
    op.drop_table("role_definitions")
