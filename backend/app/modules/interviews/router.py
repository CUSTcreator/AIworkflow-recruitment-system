"""面试 HTTP 适配层：处理各轮面试命令与页面读模型入口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from backend.app.db.session import get_db
from backend.app.models.entities import User
from backend.app.modules.applications.public import ApplicationCommandResponse, EmptyActionRequest
from backend.app.modules.auth.public import get_current_user
from backend.app.modules.interviews.schemas.command_schemas import (
    GuideConfirmRequest,
    InterviewCompleteRequest,
    InterviewPlanDraftResponse,
    InterviewProgressDraftResponse,
    InterviewWriteRequest,
    WorkflowRunRequest,
)
from backend.app.modules.interviews.commands.interview_action_commands import InterviewActionCommands
from backend.app.modules.interviews.services.post_interview_scoring_retry_service import (
    PostInterviewScoringRetryService,
)
from backend.app.modules.interviews.readModel import views_router


router = APIRouter(tags=["interviews"])
router.include_router(views_router.router)

_body = InterviewActionCommands.payload




def _queue(db, user, key, application_id, action, workflow_type, body):
    return InterviewActionCommands(db).enqueue(user=user, idempotency_key=key, application_id=application_id, action=action, workflow_type=workflow_type, body=body)


@router.post("/applications/{application_id}/workflows/first-interview-planning/run", response_model=ApplicationCommandResponse)
def run_first_planning(application_id: str, body: WorkflowRunRequest | None = None, idempotency_key: str = Header(..., alias="Idempotency-Key"), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _queue(db, user, idempotency_key, application_id, "run_first_interview_planning", "first_interview_planning_workflow", _body(body))


@router.put("/applications/{application_id}/interviews/first/plan-draft", response_model=InterviewPlanDraftResponse)
def save_first_guide_draft(application_id: str, body: GuideConfirmRequest, idempotency_key: str = Header(..., alias="Idempotency-Key"), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return InterviewActionCommands(db).save_first_guide_draft(user=user, idempotency_key=idempotency_key, application_id=application_id, body=_body(body))


@router.post("/applications/{application_id}/workflows/first-interview-planning/confirm", response_model=ApplicationCommandResponse)
def confirm_first_guide(application_id: str, body: GuideConfirmRequest | None = None, idempotency_key: str = Header(..., alias="Idempotency-Key"), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return InterviewActionCommands(db).execute_action(user=user, idempotency_key=idempotency_key, application_id=application_id, action="confirm_first_guide", body=_body(body))


@router.post("/applications/{application_id}/actions/continue-first-interview-manually", response_model=ApplicationCommandResponse)
def continue_first_interview_manually(application_id: str, body: GuideConfirmRequest | None = None, idempotency_key: str = Header(..., alias="Idempotency-Key"), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """题单生成失败或阻塞时，面试官以人工题纲继续；保留原 Workflow 记录。"""
    return InterviewActionCommands(db).execute_action(user=user, idempotency_key=idempotency_key, application_id=application_id, action="continue_first_interview_manually", body=_body(body))


@router.post("/applications/{application_id}/actions/approve-first-interview", response_model=ApplicationCommandResponse)
def approve_first_interview(application_id: str, body: EmptyActionRequest | None = None, idempotency_key: str = Header(..., alias="Idempotency-Key"), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return InterviewActionCommands(db).approve_first_interview(user=user, idempotency_key=idempotency_key, application_id=application_id, body=_body(body))


@router.post("/applications/{application_id}/actions/start-first-interview", response_model=ApplicationCommandResponse)
def start_first_interview(application_id: str, body: EmptyActionRequest | None = None, idempotency_key: str = Header(..., alias="Idempotency-Key"), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return InterviewActionCommands(db).execute_action(user=user, idempotency_key=idempotency_key, application_id=application_id, action="start_first_interview", body=_body(body))


@router.put("/applications/{application_id}/interviews/first/progress", response_model=InterviewProgressDraftResponse)
def save_first_interview_progress(application_id: str, body: InterviewWriteRequest, idempotency_key: str = Header(..., alias="Idempotency-Key"), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return InterviewActionCommands(db).save_first_progress(user=user, idempotency_key=idempotency_key, application_id=application_id, body=_body(body))


@router.post("/applications/{application_id}/actions/finish-first-interview", response_model=ApplicationCommandResponse)
def finish_first_interview(application_id: str, body: InterviewWriteRequest | None = None, idempotency_key: str = Header(..., alias="Idempotency-Key"), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return InterviewActionCommands(db).execute_action(user=user, idempotency_key=idempotency_key, application_id=application_id, action="finish_first_interview", body=_body(body))


@router.post("/applications/{application_id}/actions/submit-first-feedback", response_model=ApplicationCommandResponse)
def submit_first_feedback(application_id: str, body: InterviewCompleteRequest, idempotency_key: str = Header(..., alias="Idempotency-Key"), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _queue(db, user, idempotency_key, application_id, "submit_first_feedback", "post_first_scoring_workflow", _body(body))


@router.post("/applications/{application_id}/actions/complete-first-interview", response_model=ApplicationCommandResponse)
def complete_first_interview(application_id: str, body: InterviewCompleteRequest, idempotency_key: str = Header(..., alias="Idempotency-Key"), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return InterviewActionCommands(db).complete_first_interview(user=user, idempotency_key=idempotency_key, application_id=application_id, body=_body(body))


@router.post("/applications/{application_id}/workflows/post-first-scoring/retry", response_model=ApplicationCommandResponse)
def retry_post_first_scoring(application_id: str, body: EmptyActionRequest | None = None, idempotency_key: str = Header(..., alias="Idempotency-Key"), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """重新计算 V2：复用原始面评与冻结输入，不重复推进申请状态。"""
    return PostInterviewScoringRetryService(db).request(
        user=user, application_id=application_id, idempotency_key=idempotency_key,
        action="retry_post_first_scoring",
    )


@router.post("/applications/{application_id}/workflows/post-first-scoring/repair", response_model=ApplicationCommandResponse)
def repair_post_first_scoring(application_id: str, body: InterviewWriteRequest, idempotency_key: str = Header(..., alias="Idempotency-Key"), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """保存修正后的一面记录，并用该批新记录重新计算 V2。"""
    return PostInterviewScoringRetryService(db).request(
        user=user, application_id=application_id, idempotency_key=idempotency_key,
        action="edit_first_interview_feedback", feedback=_body(body),
    )

@router.post("/applications/{application_id}/actions/approve-second-interview", response_model=ApplicationCommandResponse)
def approve_second_interview(application_id: str, body: EmptyActionRequest | None = None, idempotency_key: str = Header(..., alias="Idempotency-Key"), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return InterviewActionCommands(db).execute_action(user=user, idempotency_key=idempotency_key, application_id=application_id, action="approve_second_interview", body=_body(body))


@router.post("/applications/{application_id}/actions/start-second-interview", response_model=ApplicationCommandResponse)
def start_second_interview(application_id: str, body: EmptyActionRequest | None = None, idempotency_key: str = Header(..., alias="Idempotency-Key"), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return InterviewActionCommands(db).execute_action(user=user, idempotency_key=idempotency_key, application_id=application_id, action="start_second_interview", body=_body(body))


@router.put("/applications/{application_id}/interviews/second/progress", response_model=InterviewProgressDraftResponse)
def save_second_interview_progress(application_id: str, body: InterviewWriteRequest, idempotency_key: str = Header(..., alias="Idempotency-Key"), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return InterviewActionCommands(db).save_second_progress(user=user, idempotency_key=idempotency_key, application_id=application_id, body=_body(body))


@router.post("/applications/{application_id}/actions/finish-second-interview", response_model=ApplicationCommandResponse)
def finish_second_interview(application_id: str, body: InterviewWriteRequest | None = None, idempotency_key: str = Header(..., alias="Idempotency-Key"), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return InterviewActionCommands(db).execute_action(user=user, idempotency_key=idempotency_key, application_id=application_id, action="finish_second_interview", body=_body(body))


@router.post("/applications/{application_id}/actions/submit-second-feedback", response_model=ApplicationCommandResponse)
def submit_second_feedback(application_id: str, body: InterviewCompleteRequest, idempotency_key: str = Header(..., alias="Idempotency-Key"), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _queue(db, user, idempotency_key, application_id, "submit_second_feedback", "post_second_scoring_workflow", _body(body))


@router.post("/applications/{application_id}/workflows/post-second-scoring/retry", response_model=ApplicationCommandResponse)
def retry_post_second_scoring(application_id: str, body: EmptyActionRequest | None = None, idempotency_key: str = Header(..., alias="Idempotency-Key"), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """重新计算 V3：复用原始面评与冻结输入，不重复推进申请状态。"""
    return PostInterviewScoringRetryService(db).request(
        user=user, application_id=application_id, idempotency_key=idempotency_key,
        action="retry_post_second_scoring",
    )


@router.post("/applications/{application_id}/workflows/post-second-scoring/repair", response_model=ApplicationCommandResponse)
def repair_post_second_scoring(application_id: str, body: InterviewWriteRequest, idempotency_key: str = Header(..., alias="Idempotency-Key"), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """保存修正后的二面记录，并用该批新记录重新计算 V3。"""
    return PostInterviewScoringRetryService(db).request(
        user=user, application_id=application_id, idempotency_key=idempotency_key,
        action="edit_second_interview_feedback", feedback=_body(body),
    )

@router.post("/applications/{application_id}/actions/complete-second-interview", response_model=ApplicationCommandResponse)
def complete_second_interview(application_id: str, body: InterviewCompleteRequest, idempotency_key: str = Header(..., alias="Idempotency-Key"), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return InterviewActionCommands(db).complete_second_interview(user=user, idempotency_key=idempotency_key, application_id=application_id, body=_body(body))



