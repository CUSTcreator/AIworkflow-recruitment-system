from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.core.security import hash_password
from backend.app.models.entities import Application, Department, Job, RoleDefinition, Task, User
from backend.app.modules.auth.authorization import ROLE_DEFAULT_PERMISSIONS


DEPARTMENTS = (
    ("DEPT_TECH", "技术部"),
    ("DEPT_PURCHASE", "采购部"),
    ("DEPT_HR", "人事部"),
)


ACCOUNTS = (
    (
        "U_DEPT_MANAGER",
        "department_manager",
        "技术部主管",
        "department_manager",
        "DEPT_TECH",
        settings.department_manager_password,
    ),
    (
        "U_DEPT_RECRUITER",
        "department_recruiter",
        "技术部招聘人",
        "department_recruiter",
        "DEPT_TECH",
        settings.department_recruiter_password,
    ),
    (
        "U_DEPT_MANAGER_PURCHASE",
        "purchase_manager",
        "采购部主管",
        "department_manager",
        "DEPT_PURCHASE",
        settings.department_manager_password,
    ),
    (
        "U_DEPT_RECRUITER_PURCHASE",
        "purchase_recruiter",
        "采购部招聘人",
        "department_recruiter",
        "DEPT_PURCHASE",
        settings.department_recruiter_password,
    ),
    ("U_HR", "hr", "HR", "hr", "DEPT_HR", settings.hr_password),
)


def seed_accounts(db: Session) -> None:
    """Create the minimum accounts required to log in; do not create business data."""
    for department_id, name in DEPARTMENTS:
        department = db.get(Department, department_id)
        if department is None:
            db.add(Department(department_id=department_id, name=name))
        else:
            department.name = name

    db.flush()
    role_names = {
        "hr": "HR",
        "department_manager": "部门主管",
        "department_recruiter": "部门招聘人员",
    }
    roles: dict[str, RoleDefinition] = {}
    # Built-in roles may have been created by an older release.  Reconcile only
    # recognizably legacy/empty permission payloads; a role that already uses
    # the current permission catalog may have been intentionally customized in
    # the admin UI and must not be overwritten on every application startup.
    legacy_permission_codes = {
        "candidate.view", "candidate.advance", "candidate.reject",
        "hard_screening.manage", "analytics.view", "system.admin",
    }
    for role_id, name in role_names.items():
        default_scope = "organization" if role_id == "hr" else "department"
        default_permissions = {
            code: True for code in ROLE_DEFAULT_PERMISSIONS[role_id]
        }
        role = db.get(RoleDefinition, role_id)
        if role is None:
            role = RoleDefinition(
                role_id=role_id,
                name=name,
                business_scope=default_scope,
                permissions=default_permissions,
                is_system=True,
                is_active=True,
            )
            db.add(role)
        elif (
            not role.permissions
            or legacy_permission_codes.intersection(role.permissions)
        ):
            role.permissions = default_permissions
            role.business_scope = default_scope
            role.name = name
            role.is_system = True
            role.is_active = True
        roles[role_id] = role

    db.flush()
    for user_id, username, display_name, role_id, department_id, password in ACCOUNTS:
        user = db.get(User, user_id)
        if user is None:
            user = User(
                user_id=user_id,
                username=username,
                password_hash=hash_password(password),
                display_name=display_name,
                role=role_id,
                role_definition_id=role_id,
                department_id=department_id,
                business_scope="organization" if role_id == "hr" else "department",
                is_active=True,
                is_system_admin=user_id == "U_HR",
                must_change_password=False,
            )
            db.add(user)
        else:
            user.username = username
            user.display_name = display_name
            user.role = role_id
            user.role_definition_id = role_id
            user.department_id = department_id
            if not user.business_scope:
                user.business_scope = "organization" if role_id == "hr" else "department"
            user.is_active = True
            if user_id == "U_HR":
                user.is_system_admin = True
            if not user.password_hash:
                user.password_hash = hash_password(password)

    # Recruiter accounts must exist before legacy applications and tasks are
    # reassigned to them; otherwise PostgreSQL can execute the UPDATE first
    # and reject it on the foreign-key constraint.
    db.flush()

    recruiters = {
        "DEPT_TECH": "U_DEPT_RECRUITER",
        "DEPT_PURCHASE": "U_DEPT_RECRUITER_PURCHASE",
    }
    for job in db.scalars(select(Job)).all():
        recruiter_id = recruiters.get(job.department_id)
        if recruiter_id and not job.department_recruiter_id:
            job.department_recruiter_id = recruiter_id
    for application in db.scalars(select(Application)).all():
        recruiter_id = recruiters.get(application.department_id)
        if recruiter_id and application.assigned_first_interviewer in {
            "U_DEPT_MANAGER",
            "U_DEPT_MANAGER_PURCHASE",
        }:
            previous_assignee = application.assigned_first_interviewer
            application.assigned_first_interviewer = recruiter_id
            for task in db.scalars(
                select(Task).where(
                    Task.application_id == application.application_id,
                    Task.assignee_user_id == previous_assignee,
                    Task.status != "done",
                )
            ).all():
                task.assignee_user_id = recruiter_id
                task.assignee_role = "department_recruiter"
    db.commit()
