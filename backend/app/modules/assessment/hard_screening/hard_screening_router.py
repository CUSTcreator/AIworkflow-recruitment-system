from __future__ import annotations

from backend.app.infrastructure.command_runtime.idempotency_guard import IdempotencyGuard

import hashlib
import json

from fastapi import APIRouter, Depends, Header

from backend.app.modules.auth.public import get_current_user
from backend.app.db.session import get_db
from backend.app.modules.assessment.hard_screening.hard_screening_schemas import (
    HardScreeningPolicyWriteRequest,
    HardScreeningReviewRequest,
)
from backend.app.models.entities import Job, User
from backend.app.infrastructure.command_runtime import CommandRunner, CommandSpec
from sqlalchemy.orm import Session
from backend.app.modules.auth.public import AuthorizationService
from backend.app.modules.assessment.hard_screening.hard_screening_service import HardScreeningService
from backend.app.modules.assessment.hard_screening.hard_screening_catalog_service import HardScreeningCatalogService
from backend.app.modules.applications.public import ApplicationAccessService, ApplicationCommandExecutor


router = APIRouter(tags=["hard-screening"])


def _key(provided: str | None, user: User, action: str, resource_id: str, body: dict) -> str:
    if provided:
        return provided
    digest = hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return IdempotencyGuard.compatibility_key(provided, user_id=user.user_id, action=action, resource_id=resource_id, body=body)


@router.get("/hard-screening-criteria")
def list_available_hard_screening_criteria(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """返回可用于配置岗位硬筛策略的已启用条件目录。"""
    AuthorizationService(db).require_business_action(user, "hard_screening.policy.manage")
    return HardScreeningCatalogService(db).list(enabled_only=True)


@router.get("/jobs/{job_id}/hard-screening-policy")
def get_hard_screening_policy(
    job_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """返回指定岗位当前生效的硬筛策略及其规则。"""
    job = db.get(Job, job_id)
    if job is not None:
        AuthorizationService(db).require_business_action(
            user, "hard_screening.policy.manage", department_id=job.department_id
        )
    return HardScreeningService(db).get_policy_view(job_id)


@router.put("/jobs/{job_id}/hard-screening-policy")
def put_hard_screening_policy(
    job_id: str,
    body: HardScreeningPolicyWriteRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """保存指定岗位的硬筛策略；修改只影响后续进入硬筛的申请。"""
    payload = body.model_dump(by_alias=False, exclude_none=True)
    def load_job(session: Session, identifier: str) -> Job | None:
        return session.query(Job).filter(Job.job_id == identifier, Job.deleted_at.is_(None)).with_for_update().one_or_none()
    def handler(context) -> dict:
        policy = HardScreeningService(db).save_policy(
            context.user, job_id, enabled=bool(context.body["enabled"]),
            rules=list(context.body["rules"]),
        )
        return HardScreeningService._policy_json(policy)
    return CommandRunner(db).execute(
        spec=CommandSpec(action="hard_screening_policy.update", resource_type="job", permission_code="hard_screening.policy.manage", audit_exempt=True),
        user=user, resource_id=job_id, body=payload,
        idempotency_key=_key(idempotency_key, user, "hard_screening_policy.update", job_id, payload),
        resource_loader=load_job, handler=handler,
    )


@router.get("/applications/{application_id}/hard-screening-result")
def get_hard_screening_result(
    application_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """返回指定申请最新一次硬筛的结论、规则明细和依据。"""
    ApplicationAccessService(db).get_visible(user, application_id)
    return HardScreeningService(db).latest_result_view(application_id)


@router.post("/applications/{application_id}/hard-screening-review")
def review_hard_screening_result(
    application_id: str,
    body: HardScreeningReviewRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """提交人工硬筛复核结论，并推进或结束当前岗位申请。"""
    payload = body.model_dump(mode="json")

    def handler(actor: User, app, request: dict):
        return HardScreeningService(db).review(
            actor,
            app,
            str(request["decision"]),
            str(request["reason"]),
        )

    authorization_action = (
        "review_hard_screening_pass"
        if body.decision == "pass"
        else "review_hard_screening_reject"
    )
    return ApplicationCommandExecutor(db).execute(
        user=user,
        idempotency_key=idempotency_key,
        action=authorization_action,
        application_id=application_id,
        body=payload,
        handler=handler,
    )


@router.post("/applications/{application_id}/hard-screening-retry")
def retry_hard_screening(
    application_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """重新运行可恢复的硬筛任务；人工复核结论仍由独立接口提交。"""

    def handler(actor: User, app, _request: dict):
        run = HardScreeningService(db).retry_for_application(app, actor)
        return {
            "applicationId": app.application_id,
            "status": app.status,
            "workflowRunId": run.workflow_run_id,
        }

    return ApplicationCommandExecutor(db).execute(
        user=user,
        idempotency_key=idempotency_key,
        action="retry_hard_screening",
        application_id=application_id,
        body={},
        handler=handler,
    )

