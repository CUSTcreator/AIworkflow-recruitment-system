"""面试页面读模型 HTTP 适配层：仅组装各面试阶段的页面数据。"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.db.session import get_db
from backend.app.models.entities import User
from backend.app.modules.applications.public import ApplicationAccessService
from backend.app.modules.auth.public import get_current_user
from backend.app.modules.auth.public import AuthorizationService, FieldRedactionService
from backend.app.modules.interviews.public import FirstInterviewQueryService, SecondInterviewQueryService
from backend.app.modules.interviews.services.first_assessment_readiness_service import FirstAssessmentReadinessService
from backend.app.shared.errors import BusinessError
from backend.app.modules.interviews.public import (
    FinalReviewView,
    FirstInterviewEvaluationView,
    FirstInterviewPlanView,
    FirstInterviewWorkspaceView,
    SecondInterviewReviewView,
    SecondInterviewWorkspaceView,
)

router = APIRouter(tags=["views"])


def _require_application_view(db: Session, user: User, application_id: str):
    """面试页面读取只要求申请可见，写操作在命令端单独鉴权。"""
    return ApplicationAccessService(db).get_visible(user, application_id)


def _redact_candidate_details(db: Session, user: User, view):
    payload = view.model_dump(mode="json") if hasattr(view, "model_dump") else view
    return FieldRedactionService().redact_candidate_details(
        AuthorizationService(db).access_context(user), payload
    )


@router.get("/applications/{application_id}/views/first-interview-plan", response_model=FirstInterviewPlanView)
def first_interview_plan(application_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """返回一面题单确认页面所需的候选人信息和面试计划。"""
    _require_application_view(db, user, application_id)
    return _redact_candidate_details(db, user, FirstInterviewQueryService(db).plan(application_id, user))


@router.get("/applications/{application_id}/views/first-interview-workspace", response_model=FirstInterviewWorkspaceView)
def first_interview_workspace(application_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """返回一面执行工作台所需的候选人背景、题单和记录内容。"""
    _require_application_view(db, user, application_id)
    return _redact_candidate_details(db, user, FirstInterviewQueryService(db).workspace(application_id, user))


@router.get("/applications/{application_id}/views/first-interview-evaluation", response_model=FirstInterviewEvaluationView)
def first_interview_evaluation(application_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """返回一面面评确认页面需要的面试记录和评价信息。"""
    _require_application_view(db, user, application_id)
    return _redact_candidate_details(db, user, FirstInterviewQueryService(db).evaluation(application_id, user))


@router.get("/applications/{application_id}/views/hr-second-review", response_model=SecondInterviewReviewView)
def hr_second_review(application_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """返回 HR 审核候选人是否进入二面的聚合展示数据。"""
    application = _require_application_view(db, user, application_id)
    readiness = FirstAssessmentReadinessService(db)
    try:
        readiness.require_ready_for_second_review(application)
    except BusinessError:
        # 处理中仍禁止进入；失败恢复码或 blocked Workflow 必须允许打开同一
        # 业务页执行后端下发的修改/重试动作，否则用户只能看到 409。
        # 这里只放行恢复页面，推进命令仍由 require_ready_for_second_review 拒绝。
        if (
            not str(application.recovery_code or "").startswith("post_first_")
            and not readiness.has_blocked_recovery_view(application)
        ):
            raise
    return _redact_candidate_details(db, user, SecondInterviewQueryService(db).review(application_id, user))



@router.get("/applications/{application_id}/views/second-interview-workspace", response_model=SecondInterviewWorkspaceView)
def second_interview_workspace(application_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """返回二面执行工作台所需的候选人背景、题单和记录内容。"""
    _require_application_view(db, user, application_id)
    return _redact_candidate_details(db, user, SecondInterviewQueryService(db).workspace(application_id, user))



@router.get("/applications/{application_id}/views/final-review", response_model=FinalReviewView)
def final_review(application_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """返回最终招聘决策页面所需的候选人完整评估汇总。"""
    _require_application_view(db, user, application_id)
    return _redact_candidate_details(db, user, SecondInterviewQueryService(db).final_review(application_id, user))
