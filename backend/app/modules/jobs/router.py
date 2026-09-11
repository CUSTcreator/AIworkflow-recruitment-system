"""岗位管理 HTTP 适配层：处理岗位查询、编辑和导入记录。"""

from __future__ import annotations

from backend.app.infrastructure.command_runtime.idempotency_guard import IdempotencyGuard

import hashlib
import json

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session

from backend.app.modules.auth.public import AuthorizationService, get_current_user
from backend.app.modules.jobs.management_schemas import (
    ImportRecordListView,
    ImportRecordReadView,
    JobManagementView,
    JobProfileRunRequest,
    JobProfileRunView,
    JobUpdateRequest,
    NavigationNotificationReadView,
    NavigationNotificationView,
)
from backend.app.db.session import get_db
from backend.app.models.entities import Job, JobVersionRecord, User, WorkflowRun
from backend.app.infrastructure.command_runtime import CommandRunner, CommandSpec
from backend.app.modules.jobs.management_read_models import RecruitmentManagementQueryService
from backend.app.modules.jobs.lifecycle_service import JobEditingService, JobLifecycleService
from backend.app.modules.jobs.services.job_profile_service import JobProfileService
from backend.app.modules.jobs.access_service import JobAccessService
from backend.app.shared.workflows.execution_timeline import WorkflowExecutionTimelineQuery


router = APIRouter(tags=["recruitment-management"])
def _load_active_job(db: Session, job_id: str) -> Job | None:
    """CommandRunner 的岗位资源加载器：锁定当前可编辑的岗位。"""
    return db.query(Job).filter(Job.job_id == job_id, Job.deleted_at.is_(None)).with_for_update().one_or_none()
def _load_job_including_deleted(db: Session, job_id: str) -> Job | None:
    """删除命令的重放加载器：保留已软删行，才能返回既有幂等响应。"""
    return db.query(Job).filter(Job.job_id == job_id).with_for_update().one_or_none()


def _key(provided: str | None, user: User, action: str, resource_id: str, body: dict) -> str:
    if provided:
        return provided
    digest = hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return IdempotencyGuard.compatibility_key(provided, user_id=user.user_id, action=action, resource_id=resource_id, body=body)


@router.get("/job-management/jobs", response_model=list[JobManagementView])
def list_managed_jobs(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return RecruitmentManagementQueryService(db).list_jobs(user)


@router.get("/job-management/jobs/{job_id}", response_model=JobManagementView)
def get_managed_job(
    job_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return RecruitmentManagementQueryService(db).get_job(user, job_id)


@router.get("/job-management/jobs/{job_id}/profile-runs/{workflow_run_id}/timeline")
def get_job_profile_timeline(
    job_id: str,
    workflow_run_id: str,
    limit: int = Query(default=30, ge=1, le=50),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """按岗位数据范围返回安全执行轨迹，不暴露管理员技术日志。"""
    job = db.get(Job, job_id)
    if job is None or job.deleted_at is not None:
        raise HTTPException(status_code=404, detail="岗位不存在")
    JobAccessService(db).assert_visible(user, job)
    run = db.get(WorkflowRun, workflow_run_id)
    version = db.get(JobVersionRecord, run.subject_id) if run is not None else None
    if (
        run is None
        or run.workflow_type != "job_profile_compilation_workflow"
        or version is None
        or version.job_id != job_id
    ):
        raise HTTPException(status_code=404, detail="岗位未关联指定画像任务")
    return {
        "items": WorkflowExecutionTimelineQuery(db).for_workflow_run(
            workflow_run_id=workflow_run_id, limit=limit,
        )
    }

@router.patch("/job-management/jobs/{job_id}", response_model=JobManagementView)
def update_managed_job(
    job_id: str,
    request: JobUpdateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    payload = request.model_dump()
    CommandRunner(db).execute(
        spec=CommandSpec(action="job.update", resource_type="job", permission_code="job.edit", audit_exempt=True),
        user=user, resource_id=job_id, body=payload,
        idempotency_key=_key(idempotency_key, user, "job.update", job_id, payload),
        resource_loader=_load_active_job,
        handler=lambda context: ({"job_id": JobEditingService(db).update(context.user, job_id, context.body).job_id}),
    )
    return RecruitmentManagementQueryService(db).get_job(user, job_id)

def _request_job_profile_run(
    *,
    db: Session,
    user: User,
    job_id: str,
    mode: str,
    reason: str | None,
    idempotency_key: str | None,
) -> dict:
    """同步保存“重试/重新生成”命令并入队；真正编译仍由异步 Workflow 完成。"""
    payload = {"mode": mode, "reason": reason or ""}
    return CommandRunner(db).execute(
        spec=CommandSpec(
            action=f"job.profile.{mode}",
            resource_type="job",
            permission_code="job.edit",
        ),
        user=user,
        resource_id=job_id,
        body=payload,
        idempotency_key=_key(idempotency_key, user, f"job.profile.{mode}", job_id, payload),
        resource_loader=_load_active_job,
        handler=lambda context: _job_profile_run_response(
            job_id=job_id,
            mode=mode,
            run=JobProfileService(db).request_profile_run(
                job_id=job_id,
                triggered_by=context.user.user_id,
                mode=mode,
            ),
        ),
    )


def _job_profile_run_response(*, job_id: str, mode: str, run) -> dict:
    return {
        "job_id": job_id,
        "jd_version_id": run.subject_id,
        "workflow_run_id": run.workflow_run_id,
        "profile_status": "queued",
        "action": mode,
    }


@router.post(
    "/job-management/jobs/{job_id}/profile-runs/retry",
    response_model=JobProfileRunView,
)
def retry_job_profile(
    job_id: str,
    request: JobProfileRunRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """只允许失败的当前 JD 版本再次入队。"""
    return _request_job_profile_run(
        db=db, user=user, job_id=job_id, mode="retry", reason=request.reason,
        idempotency_key=idempotency_key,
    )


@router.post(
    "/job-management/jobs/{job_id}/profile-runs/regenerate",
    response_model=JobProfileRunView,
)
def regenerate_job_profile(
    job_id: str,
    request: JobProfileRunRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """对已就绪画像创建新 revision；旧画像在新版本成功前仍保持可用。"""
    return _request_job_profile_run(
        db=db, user=user, job_id=job_id, mode="regenerate", reason=request.reason,
        idempotency_key=idempotency_key,
    )
@router.delete("/job-management/jobs/{job_id}")
def delete_managed_job(
    job_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return CommandRunner(db).execute(
        spec=CommandSpec(action="job.delete", resource_type="job", permission_code="job.delete", audit_exempt=True),
        user=user, resource_id=job_id, body={},
        idempotency_key=_key(idempotency_key, user, "job.delete", job_id, {}),
        resource_loader=_load_job_including_deleted,
        handler=lambda context: JobLifecycleService(db).soft_delete(context.user, job_id),
    )


@router.get("/import-records", response_model=ImportRecordListView)
def list_import_records(
    import_type: str | None = Query(default=None, alias="type", pattern="^(job|resume)$"),
    status: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=100, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return RecruitmentManagementQueryService(db).list_import_records(
        user,
        import_type=import_type,
        status=status,
        limit=limit,
    )


@router.post("/import-records/read", response_model=ImportRecordReadView)
def mark_import_records_read(
    import_type: str = Query(alias="type", pattern="^(job|resume)$"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response = CommandRunner(db).execute(
        spec=CommandSpec(
            action="recruitment_import.mark_read",
            resource_type="system",
            permission_code=None,
            authorization_mode="authenticated_self",
            idempotent=False,
            audit_exempt=True,
        ),
        user=user, resource_id=user.user_id, body={"type": import_type},
        handler=lambda context: _read_import_records(db, context.user, import_type),
    )
    return ImportRecordReadView(**response)


def _read_import_records(db: Session, user: User, import_type: str) -> dict:
    cursor = RecruitmentManagementQueryService(db).mark_import_records_read(
        user, import_type, commit=False
    )
    return {"import_type": cursor.channel, "last_read_at": cursor.last_read_at}


@router.get("/navigation-notifications", response_model=NavigationNotificationView)
def get_navigation_notifications(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    task_count, candidate_count = (
        RecruitmentManagementQueryService(db).navigation_notification_counts(user)
    )
    return NavigationNotificationView(
        task_unread_count=task_count,
        candidate_unread_count=candidate_count,
    )


@router.post(
    "/navigation-notifications/read",
    response_model=NavigationNotificationReadView,
)
def mark_navigation_notification_read(
    channel: str = Query(pattern="^(task|candidate_application)$"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response = CommandRunner(db).execute(
        spec=CommandSpec(
            action="navigation_notification.mark_read",
            resource_type="system",
            permission_code=None,
            authorization_mode="authenticated_self",
            idempotent=False,
            audit_exempt=True,
        ),
        user=user, resource_id=user.user_id, body={"channel": channel},
        handler=lambda context: _read_navigation_channel(db, context.user, channel),
    )
    return NavigationNotificationReadView(**response)


def _read_navigation_channel(db: Session, user: User, channel: str) -> dict:
    cursor = RecruitmentManagementQueryService(db).mark_navigation_channel_read(
        user, channel, commit=False
    )
    return {"channel": cursor.channel, "last_read_at": cursor.last_read_at}
