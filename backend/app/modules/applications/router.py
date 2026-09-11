"""岗位申请 HTTP 适配层：处理申请状态、列表、时间线和附件入口。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from backend.app.db.session import get_db
from backend.app.models.entities import User
from backend.app.modules.applications.schemas.command_schemas import (
    ApplicationCommandResponse,
    DepartmentOption,
    FinalDecisionRequest,
    ReviewDecisionRequest,
)
from backend.app.modules.applications.commands.application_write_commands import ApplicationWriteCommands
from backend.app.modules.applications.schemas.command_schemas import JobProfileRecoveryRequest
from backend.app.modules.applications.queries.application_action_queries import ApplicationActionQueryService
from backend.app.modules.applications.queries.read_models import ApplicationQueryService
from backend.app.modules.applications.schemas.list_schemas import (
    ApplicationFilterOptionsView,
    ApplicationListView,
)
from backend.app.modules.auth.public import get_current_user
from backend.app.modules.applications.documents import document_router


router = APIRouter(tags=["applications"])


@router.get("/applications/filter-options", response_model=ApplicationFilterOptionsView)
def application_filter_options(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """返回当前用户可见申请涉及的部门和岗位筛选项。"""
    return ApplicationQueryService(db).filter_options(user)


@router.get("/departments", response_model=list[DepartmentOption])
def list_departments(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """返回可选择的部门列表，供岗位和候选人页面的筛选项使用。"""
    return ApplicationActionQueryService(db).departments()


@router.get("/applications/{application_id}/resume-pdf")
def resume_pdf(application_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """返回指定申请关联的原始简历 PDF，供页面在线预览。"""
    path = ApplicationActionQueryService(db).resume_pdf_path(user=user, application_id=application_id)
    return FileResponse(path, media_type="application/pdf", filename=path.name, content_disposition_type="inline")




@router.get("/applications/{application_id}/timeline")
def recruitment_timeline(application_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """获取指定申请从投递到当前阶段的招聘流程时间线。"""
    return ApplicationActionQueryService(db).timeline(user=user, application_id=application_id)


@router.get("/applications/{application_id}/workflow-timeline")
def workflow_timeline(
    application_id: str,
    workflow_run_id: str = Query(alias="workflowRunId", min_length=1, max_length=64),
    limit: int = Query(default=20, ge=1, le=50),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """返回当前申请关联任务的简版执行轨迹，不替代招聘业务时间线。"""
    return ApplicationActionQueryService(db).workflow_timeline(
        user=user,
        application_id=application_id,
        workflow_run_id=workflow_run_id,
        limit=limit,
    )


@router.get("/applications", response_model=ApplicationListView)
def list_applications(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, alias="pageSize", ge=1, le=100),
    status: str | None = Query(default=None),
    group: Literal["in_progress", "passed", "rejected", "cancelled"] | None = Query(default=None),
    job_id: str | None = Query(default=None, alias="jobId"),
    department_id: str | None = Query(default=None, alias="departmentId"),
    hard_screening_status: Literal["not_configured", "pending", "running", "processing", "passed", "failed", "manual_review"] | None = Query(default=None, alias="hardScreeningStatus"),
    highest_degree: str | None = Query(default=None, alias="highestDegree", max_length=64),
    major_keyword: str | None = Query(default=None, alias="majorKeyword", max_length=100),
    minimum_score: float | None = Query(default=None, alias="minimumScore", ge=0, le=100),
    overdue_only: bool = Query(default=False, alias="overdueOnly"),
    attention_only: bool = Query(default=False, alias="attentionOnly"),
    submitted_from: datetime | None = Query(default=None, alias="submittedFrom"),
    submitted_to: datetime | None = Query(default=None, alias="submittedTo"),
    sort_by: Literal["submittedAt", "currentScore", "updatedAt"] = Query(default="submittedAt", alias="sortBy"),
    sort_order: Literal["asc", "desc"] = Query(default="desc", alias="sortOrder"),
    keyword: str | None = Query(default=None, max_length=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """按筛选、排序和分页条件返回招聘流程中的候选人申请列表。"""
    return ApplicationQueryService(db).list_applications(
        user, page=page, page_size=page_size, status=status, group=group, job_id=job_id,
        department_id=department_id, hard_screening_status=hard_screening_status,
        highest_degree=highest_degree, major_keyword=major_keyword,
        minimum_score=minimum_score, overdue_only=overdue_only,
        attention_only=attention_only, submitted_from=submitted_from,
        submitted_to=submitted_to, sort_by=sort_by, sort_order=sort_order, keyword=keyword,
    )


@router.delete("/applications/{application_id}")
def delete_application(
    application_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """从招聘流程中移除指定申请，不直接删除候选人及其历史材料。"""
    return ApplicationWriteCommands(db).delete(
        user=user,
        application_id=application_id,
        idempotency_key=idempotency_key or f"legacy:application.delete:{user.user_id}:{application_id}",
    )


@router.post(
    "/applications/{application_id}/initial-assessment/retry",
    response_model=ApplicationCommandResponse,
)
def retry_initial_assessment(
    application_id: str,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """岗位画像已就绪时，只为当前申请重新启动硬筛或 V1。"""
    return ApplicationWriteCommands(db).retry_initial_assessment(
        user=user,
        application_id=application_id,
        idempotency_key=idempotency_key,
    )


@router.post(
    "/applications/{application_id}/recovery/job-profile",
    response_model=ApplicationCommandResponse,
)
def recover_application_job_profile(
    application_id: str,
    body: JobProfileRecoveryRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """针对该投递冻结的 JD 恢复岗位画像，而不是默认操作岗位最新版本。"""
    return ApplicationWriteCommands(db).recover_job_profile(
        user=user,
        application_id=application_id,
        idempotency_key=idempotency_key,
        mode=body.mode,
    )




@router.post("/applications/{application_id}/actions/final-decision", response_model=ApplicationCommandResponse)
def final_decision(application_id: str, body: FinalDecisionRequest, idempotency_key: str = Header(..., alias="Idempotency-Key"), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """提交最终招聘决定，例如录用或最终淘汰候选人。"""
    facade = ApplicationWriteCommands(db)
    return facade.execute_decision(user=user, idempotency_key=idempotency_key, application_id=application_id, action="final_decision", body=facade.payload(body))


@router.post("/applications/{application_id}/actions/department-decision", response_model=ApplicationCommandResponse)
def department_decision(application_id: str, body: ReviewDecisionRequest, idempotency_key: str = Header(..., alias="Idempotency-Key"), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """提交部门审核决定，推进候选人或结束其招聘流程。"""
    facade = ApplicationWriteCommands(db)
    return facade.execute_decision(user=user, idempotency_key=idempotency_key, application_id=application_id, action="department_decision", body=facade.payload(body))


@router.post("/applications/{application_id}/actions/hr-decision", response_model=ApplicationCommandResponse)
def hr_decision(application_id: str, body: ReviewDecisionRequest, idempotency_key: str = Header(..., alias="Idempotency-Key"), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """提交 HR 审核决定，决定是否进入二面或结束流程。"""
    facade = ApplicationWriteCommands(db)
    return facade.execute_decision(user=user, idempotency_key=idempotency_key, application_id=application_id, action="hr_decision", body=facade.payload(body))


# The module owns all application-related HTTP entry points.  The v1 API
# registry imports this single router rather than these internal subrouters.
router.include_router(document_router.router)
