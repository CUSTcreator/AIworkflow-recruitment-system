"""初筛评估 HTTP 适配层：处理初筛、硬筛、证据和评分任务请求。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from backend.app.db.session import get_db
from backend.app.models.entities import User
from backend.app.modules.applications.public import ApplicationAccessService
from backend.app.modules.applications.public import ApplicationCommandResponse
from backend.app.modules.assessment.public import ScreeningQueryService
from backend.app.modules.assessment.schemas.view_schemas import EvidenceDetailView, ScreeningReviewView
from backend.app.modules.assessment.commands.assessment_action_commands import AssessmentActionCommands

from backend.app.modules.assessment.schemas.view_schemas import ScoringStatusView
from backend.app.modules.assessment.schemas.command_schemas import ScoringWorkflowRunRequest
from backend.app.modules.auth.public import get_current_user
from backend.app.modules.auth.public import AuthorizationService, FieldRedactionService
from backend.app.modules.assessment.hard_screening import hard_screening_catalog_router, hard_screening_router


router = APIRouter(tags=["assessment"])


def _require_screening_review_view(
    db: Session, user: User, application_id: str
) -> None:
    """初筛工作台是读取页面，只要求当前用户可见该申请。

    部门审核、面试结论等敏感写入仍在对应 Command 中按阶段原子权限
    业务权限再次校验；不能因为用户仅查看页面而获得这些写权限。
    """
    ApplicationAccessService(db).get_visible(user, application_id)


def _require_assessment_detail_permission(
    db: Session, user: User, application_id: str
) -> None:
    """评分、证据等评估详情随申请可见性继承。"""
    ApplicationAccessService(db).get_visible(user, application_id)


def _redact_candidate_details(db: Session, user: User, dto):
    payload = dto.model_dump(mode="json") if hasattr(dto, "model_dump") else dto
    if not isinstance(payload, dict):
        return payload
    authorization = AuthorizationService(db)
    return FieldRedactionService().redact_candidate_details(
        authorization.access_context(user),
        payload,
    )



@router.get("/applications/{application_id}/screening-result")
def screening_result(
    application_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """保留旧 URL，返回指定申请的初筛结果。"""
    _require_assessment_detail_permission(db, user, application_id)
    return _redact_candidate_details(db, user, ScreeningQueryService(db).screening_result(application_id))


@router.get(
    "/applications/{application_id}/views/screening-review",
    response_model=ScreeningReviewView,
)
def screening_review(
    application_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """返回初筛工作台所需的候选人、岗位、评分和决策数据。"""
    _require_screening_review_view(db, user, application_id)
    return _redact_candidate_details(db, user, ScreeningQueryService(db).screening_review(application_id, user))


@router.get(
    "/applications/{application_id}/evidence/{evidence_id}",
    response_model=EvidenceDetailView,
)
def evidence_detail(
    application_id: str,
    evidence_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """返回初筛评分证据的原文定位信息。"""
    _require_assessment_detail_permission(db, user, application_id)
    return _redact_candidate_details(db, user, ScreeningQueryService(db).evidence_detail(application_id, evidence_id))


@router.get("/applications/{application_id}/workflows/scoring/status", response_model=ScoringStatusView)
def scoring_status(application_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """返回指定申请的初筛任务执行状态，供页面轮询处理进度。"""
    ApplicationAccessService(db).get_visible(user, application_id)
    return ScreeningQueryService(db).scoring_status(application_id, user=user)


@router.get("/workflow-runs/{workflow_run_id}", response_model=ScoringStatusView)
def workflow_run_status(workflow_run_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """根据后台任务编号返回其所属申请和当前执行状态。"""
    return AssessmentActionCommands(db).workflow_status(workflow_run_id, user)


@router.post("/applications/{application_id}/workflows/scoring/run", response_model=ApplicationCommandResponse)
def run_scoring(
    application_id: str,
    body: ScoringWorkflowRunRequest | None = None,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """为指定申请提交初筛评分任务，并保证重复请求不会重复执行。"""
    return AssessmentActionCommands(db).request_scoring(application_id, user, idempotency_key, body)


@router.post("/applications/{application_id}/workflows/scoring/rebuild", response_model=ApplicationCommandResponse)
def rebuild_screening_assessment(
    application_id: str,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """为 V2/V3 的来源故障创建新的 V1，不回退当前招聘阶段。"""
    return AssessmentActionCommands(db).rebuild_screening_assessment(
        application_id, user, idempotency_key,
    )


# assessment 模块的全部 HTTP 接口统一由此公开入口注册。
router.include_router(hard_screening_router.router)
router.include_router(hard_screening_catalog_router.router)
