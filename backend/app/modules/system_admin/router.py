"""系统管理 HTTP 适配层：仅提供管理员账号、组织与运维操作。"""

from __future__ import annotations

from backend.app.infrastructure.command_runtime.idempotency_guard import IdempotencyGuard

import hashlib
import json

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.orm import Session

from backend.app.modules.auth.public import require_system_admin
from backend.app.db.session import get_db
from backend.app.models.entities import Application, AuditEvent, Department, Job, RoleDefinition, User, WorkflowRun
from backend.app.modules.system_admin.schemas import (
    AdminAuditEventPage,
    AdminJobUpdate,
    AdminUserCreate,
    AdminUserUpdate,
    AdminWorkflowPage,
    WorkflowSummaryView,
    DepartmentCreate,
    DepartmentUpdate,
    ObjectCleanupTaskView,
    PasswordResetRequest,
    RoleCreate,
    RoleUpdate,
    WorkflowExecutionEventPage,
    WorkflowRetryRequest,
)
from backend.app.modules.system_admin.account_service import AccountAdminService
from backend.app.modules.system_admin.operations_service import OperationsAdminService
from backend.app.modules.system_admin.organization_service import OrganizationAdminService
from backend.app.modules.jobs.public import JobLifecycleService
from backend.app.modules.applications.public import ApplicationLifecycleCommands
from backend.app.infrastructure.command_runtime import CommandRunner, CommandSpec


router = APIRouter(prefix="/admin", tags=["system-admin"])


def _accounts(db: Session) -> AccountAdminService:
    return AccountAdminService(db)

def _organization(db: Session) -> OrganizationAdminService:
    return OrganizationAdminService(db)

def _operations(db: Session) -> OperationsAdminService:
    return OperationsAdminService(db)


def _command_key(
    provided: str | None, *, actor: User, action: str, resource_id: str, body: dict
) -> str:
    if provided:
        return provided
    digest = hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return IdempotencyGuard.compatibility_key(provided, user_id=actor.user_id, action=action, resource_id=resource_id, body=body)


def _system_command(
    *, db: Session, actor: User, action: str, resource_id: str, body: dict,
    idempotency_key: str | None, handler, resource_loader=None,
):
    return CommandRunner(db).execute(
        spec=CommandSpec(
            action=action,
            resource_type="system",
            permission_code=None,
            requires_system_admin=True,
            audit_exempt=True,
        ),
        user=actor,
        resource_id=resource_id,
        body=body,
        idempotency_key=_command_key(
            idempotency_key, actor=actor, action=action, resource_id=resource_id, body=body
        ),
        resource_loader=resource_loader,
        handler=handler,
    )


def _role_loader(db: Session, role_id: str):
    return db.query(RoleDefinition).filter(RoleDefinition.role_id == role_id).with_for_update().one_or_none()


def _user_loader(db: Session, user_id: str):
    return db.query(User).filter(User.user_id == user_id).with_for_update().one_or_none()


def _department_loader(db: Session, department_id: str):
    return db.query(Department).filter(
        Department.department_id == department_id
    ).with_for_update().one_or_none()


def _job_loader(db: Session, job_id: str):
    return db.query(Job).filter(Job.job_id == job_id).with_for_update().one_or_none()


def _workflow_loader(db: Session, run_id: str):
    return db.query(WorkflowRun).filter(
        WorkflowRun.workflow_run_id == run_id
    ).with_for_update().one_or_none()


def _application_loader(db: Session, application_id: str):
    return db.query(Application).filter(
        Application.application_id == application_id
    ).with_for_update().one_or_none()


def _cleanup_task_loader(db: Session, cleanup_task_id: str):
    return db.query(AuditEvent).filter(
        AuditEvent.audit_event_id == cleanup_task_id,
        AuditEvent.action == "application.hard_delete",
    ).with_for_update().one_or_none()


@router.get("/roles")
def list_roles(
    _: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
):
    return _accounts(db).list_roles()


@router.post("/roles")
def create_role(
    body: RoleCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
):
    # 仅保留调用方实际提交的字段，才能区分新职责包请求和旧原子权限请求。
    payload = body.model_dump(exclude_unset=True)
    return _system_command(
        db=db, actor=actor, action="admin.role.create", resource_id="roles",
        body=payload, idempotency_key=idempotency_key,
        handler=lambda context: _accounts(db).create_role(context.user, payload, commit=False),
    )


@router.patch("/roles/{role_id}")
def update_role(
    role_id: str,
    body: RoleUpdate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
):
    payload = body.model_dump(exclude_unset=True)
    return _system_command(
        db=db, actor=actor, action="admin.role.update", resource_id=role_id,
        body=payload, idempotency_key=idempotency_key, resource_loader=_role_loader,
        handler=lambda context: _accounts(db).update_role(context.user, role_id, payload, commit=False),
    )


@router.delete("/roles/{role_id}")
def delete_role(
    role_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
):
    return _system_command(
        db=db, actor=actor, action="admin.role.delete", resource_id=role_id,
        body={}, idempotency_key=idempotency_key, resource_loader=_role_loader,
        handler=lambda context: _accounts(db).delete_role(context.user, role_id, commit=False),
    )


@router.get("/users")
def list_users(
    keyword: str = Query(default="", max_length=100),
    _: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
):
    return _accounts(db).list_users(keyword)


@router.post("/users")
def create_user(
    body: AdminUserCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
):
    # 保留调用方实际提交的覆盖字段，使旧原子权限客户端和新职责包客户端
    # 在过渡期间能够被服务层准确区分。
    raw = body.model_dump(exclude_unset=True)
    payload = {key: value for key, value in raw.items() if key != "password"}
    payload["passwordChanged"] = True
    return _system_command(
        db=db, actor=actor, action="admin.user.create", resource_id="users",
        body=payload, idempotency_key=idempotency_key,
        handler=lambda context: _accounts(db).create_user(context.user, raw, commit=False),
    )


@router.patch("/users/{user_id}")
def update_user(
    user_id: str,
    body: AdminUserUpdate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
):
    payload = body.model_dump(exclude_unset=True)
    return _system_command(
        db=db, actor=actor, action="admin.user.update", resource_id=user_id,
        body=payload, idempotency_key=idempotency_key, resource_loader=_user_loader,
        handler=lambda context: _accounts(db).update_user(context.user, user_id, payload, commit=False),
    )


@router.delete("/users/{user_id}")
def delete_user(
    user_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
):
    return _system_command(
        db=db, actor=actor, action="admin.user.delete", resource_id=user_id,
        body={}, idempotency_key=idempotency_key, resource_loader=_user_loader,
        handler=lambda context: _accounts(db).soft_delete_user(context.user, user_id, commit=False),
    )


@router.post("/users/{user_id}/reset-password")
def reset_password(
    user_id: str,
    body: PasswordResetRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
):
    payload = {"passwordChanged": True}
    return _system_command(
        db=db, actor=actor, action="admin.user.reset_password", resource_id=user_id,
        body=payload, idempotency_key=idempotency_key, resource_loader=_user_loader,
        handler=lambda context: _accounts(db).reset_password(
            context.user, user_id, body.password, commit=False
        ),
    )


@router.get("/departments")
def list_departments(
    _: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
):
    return _organization(db).list_departments()


@router.post("/departments")
def create_department(
    body: DepartmentCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
):
    payload = body.model_dump(exclude_unset=True)
    return _system_command(
        db=db, actor=actor, action="admin.department.create", resource_id="departments",
        body=payload, idempotency_key=idempotency_key,
        handler=lambda context: _organization(db).create_department(
            context.user, body.name, commit=False
        ),
    )


@router.patch("/departments/{department_id}")
def update_department(
    department_id: str,
    body: DepartmentUpdate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
):
    payload = body.model_dump(exclude_unset=True)
    return _system_command(
        db=db, actor=actor, action="admin.department.update", resource_id=department_id,
        body=payload, idempotency_key=idempotency_key, resource_loader=_department_loader,
        handler=lambda context: _organization(db).update_department(
            context.user, department_id, payload, commit=False
        ),
    )


@router.delete("/departments/{department_id}")
def delete_department(
    department_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
):
    return _system_command(
        db=db, actor=actor, action="admin.department.delete", resource_id=department_id,
        body={}, idempotency_key=idempotency_key, resource_loader=_department_loader,
        handler=lambda context: _organization(db).soft_delete_department(
            context.user, department_id, commit=False
        ),
    )


@router.get("/jobs")
def list_jobs(
    _: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
):
    return _organization(db).list_jobs()


@router.patch("/jobs/{job_id}")
def update_job(
    job_id: str,
    body: AdminJobUpdate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
):
    payload = body.model_dump(exclude_unset=True)
    return _system_command(
        db=db, actor=actor, action="admin.job.update", resource_id=job_id,
        body=payload, idempotency_key=idempotency_key, resource_loader=_job_loader,
        handler=lambda context: _organization(db).update_job(
            context.user, job_id, payload, commit=False
        ),
    )


@router.delete("/jobs/{job_id}")
def delete_job(
    job_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
):
    return _system_command(
        db=db, actor=actor, action="admin.job.delete", resource_id=job_id,
        body={}, idempotency_key=idempotency_key, resource_loader=_job_loader,
        handler=lambda context: JobLifecycleService(db).soft_delete(
            context.user, job_id
        ),
    )


@router.get("/workflow-runs/summary", response_model=WorkflowSummaryView)
def workflow_summary(
    _: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
):
    return _operations(db).workflow_summary()


@router.get("/workflow-runs", response_model=AdminWorkflowPage)
def list_workflows(
    status: str = Query(default="", max_length=32),
    cursor: str = Query(default="", max_length=512),
    limit: int = Query(default=50, ge=1, le=100),
    _: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
):
    return _operations(db).list_workflows(status=status, cursor=cursor, limit=limit)


@router.get(
    "/workflow-runs/{run_id}/execution-events",
    response_model=WorkflowExecutionEventPage,
)
def list_workflow_execution_events(
    run_id: str,
    cursor: str = Query(default="", max_length=512),
    limit: int = Query(default=50, ge=1, le=100),
    _: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
):
    """返回一条后台任务的安全执行轨迹；不暴露原始诊断字段。"""
    return _operations(db).list_workflow_execution_events(
        run_id=run_id, cursor=cursor, limit=limit
    )

@router.post("/workflow-runs/{run_id}/retry")
def retry_workflow(
    run_id: str,
    body: WorkflowRetryRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
):
    payload = body.model_dump()
    return _system_command(
        db=db, actor=actor, action="admin.workflow.retry", resource_id=run_id,
        body=payload, idempotency_key=idempotency_key, resource_loader=_workflow_loader,
        handler=lambda context: _operations(db).retry_workflow(
            context.user, run_id, body.reason, commit=False
        ),
    )


@router.get("/audit-events", response_model=AdminAuditEventPage)
def list_audit_events(
    action: str = Query(default="", max_length=96),
    category: str = Query(default="", max_length=32),
    query_text: str = Query(default="", alias="query", max_length=160),
    actor_user_id: str = Query(default="", alias="actorUserId", max_length=64),
    from_at: str = Query(default="", alias="from", max_length=64),
    to_at: str = Query(default="", alias="to", max_length=64),
    cursor: str = Query(default="", max_length=512),
    limit: int = Query(default=50, ge=1, le=100),
    _: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
):
    return _operations(db).list_audit_events(
        action=action,
        category=category,
        query_text=query_text,
        actor_user_id=actor_user_id,
        from_at=from_at,
        to_at=to_at,
        cursor=cursor,
        limit=limit,
    )

@router.get("/deleted-applications")
def list_deleted_applications(
    cursor: str = Query(default="", max_length=512),
    limit: int = Query(default=50, ge=1, le=100),
    _: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
):
    return ApplicationLifecycleCommands(db).list_deleted(cursor=cursor, limit=limit)


@router.get("/object-cleanup-tasks", response_model=list[ObjectCleanupTaskView])
def list_object_cleanup_tasks(
    _: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
):
    return _operations(db).list_object_cleanup_tasks()


@router.post("/object-cleanup-tasks/{cleanup_task_id}/retry")
def retry_object_cleanup(
    cleanup_task_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
):
    return _system_command(
        db=db,
        actor=actor,
        action="admin.object_cleanup.retry",
        resource_id=cleanup_task_id,
        body={},
        idempotency_key=idempotency_key,
        resource_loader=_cleanup_task_loader,
        handler=lambda context: _retry_object_cleanup_command(context, cleanup_task_id),
    )


@router.delete("/applications/{application_id}")
def permanently_delete_application(
    application_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
):
    return _system_command(
        db=db, actor=actor, action="application.hard_delete", resource_id=application_id,
        body={}, idempotency_key=idempotency_key, resource_loader=_application_loader,
        handler=lambda context: _hard_delete_command(context, application_id),
    )


def _hard_delete_command(context, application_id: str) -> dict:
    result = ApplicationLifecycleCommands(context.db).hard_delete(
        context.user, application_id
    )
    cleanup_audit_event_id = str(result.pop("_cleanup_audit_event_id"))
    cleanup_warnings = result["cleanupWarnings"]

    def cleanup() -> None:
        cleanup_result = _operations(context.db).execute_object_cleanup(cleanup_audit_event_id)
        if cleanup_result["status"] == "failed":
            cleanup_warnings.append("部分文件尚未清理，可在系统管理页重试。")

    context.after_commit(cleanup)
    return result


def _retry_object_cleanup_command(context, cleanup_task_id: str) -> dict:
    result = _operations(context.db).request_object_cleanup_retry(
        context.user, cleanup_task_id, commit=False
    )
    context.after_commit(
        lambda: _operations(context.db).execute_object_cleanup(cleanup_task_id)
    )
    return result
