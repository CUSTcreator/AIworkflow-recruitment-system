"""Remove workflow archetypes from roles and rename department supervisors.

Revision ID: 032_permission_only_roles
Revises: 031_role_management
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "032_permission_only_roles"
down_revision = "031_role_management"
branch_labels = None
depends_on = None


def upgrade() -> None:
    roles = sa.table(
        "role_definitions",
        sa.column("role_id", sa.String()),
        sa.column("permissions", sa.JSON()),
    )
    permission_updates = {
        "hr": ["resume.upload", "job_document.upload", "job_document.confirm", "job.edit", "job.delete", "hard_screening.manage", "screening.run", "candidate.view", "candidate.department.view", "candidate.detail.view", "analytics.view", "candidate.advance", "candidate.reject", "application.delete", "candidate_document.upload", "candidate_document.manage", "second_interview.manage", "final_decision.manage"],
        "department_recruiter": ["resume.upload", "candidate.view", "candidate.detail.view", "candidate.advance", "candidate.reject", "first_interview.manage", "candidate_document.upload"],
        "department_manager": ["candidate.view", "candidate.department.view", "job.hiring_manager.assign", "analytics.view"],
    }
    connection = op.get_bind()
    for role_id, permissions in permission_updates.items():
        connection.execute(
            roles.update().where(roles.c.role_id == role_id).values(
                permissions={code: True for code in permissions}
            )
        )
    op.execute(sa.text(
        "UPDATE role_definitions SET name = '部门主管' WHERE role_id = 'department_manager'"
    ))
    op.execute(sa.text(
        "UPDATE users SET display_name = replace(display_name, '负责人', '主管') "
        "WHERE display_name LIKE '%负责人%'"
    ))
    with op.batch_alter_table("role_definitions") as batch:
        batch.drop_index("ix_role_definitions_base_role")
        batch.drop_column("base_role")


def downgrade() -> None:
    with op.batch_alter_table("role_definitions") as batch:
        batch.add_column(sa.Column("base_role", sa.String(length=64), nullable=True))
        batch.create_index("ix_role_definitions_base_role", ["base_role"])
    op.execute(sa.text(
        "UPDATE role_definitions SET base_role = role_id "
        "WHERE role_id IN ('hr', 'department_manager', 'department_recruiter')"
    ))
