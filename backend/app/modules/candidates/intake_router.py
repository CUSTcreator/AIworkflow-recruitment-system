from __future__ import annotations

from datetime import datetime
import hashlib
import json
from typing import Literal

from fastapi import APIRouter, Depends, File, Header, Query, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.db.session import get_db
from backend.app.models.entities import ResumeSubmission, User, WorkflowRun
from backend.app.infrastructure.command_runtime import CommandRunner, CommandSpec
from backend.app.modules.auth.public import get_current_user
from backend.app.modules.auth.domain.permission_catalog import (
    RESUME_SUBMISSION_ACTION_REQUIREMENTS,
)
from backend.app.modules.document_ingestion.public import ResumeDocumentService
from backend.app.shared.workflows.execution_timeline import WorkflowExecutionTimelineQuery
from backend.app.shared.recovery_actions import RecoveryActionView

from .intake_read_models import CandidateIntakeQueryService
from .resume_submission_status import ResumeSubmissionStatus
from .rebuild_service import CandidateResumeRebuildService
from .candidate_routing_service import CandidateRoutingService


router = APIRouter(prefix="/candidate-intakes", tags=["candidate-intakes"])
def _load_resume_submission(db: Session, submission_id: str) -> ResumeSubmission | None:
    """简历处理命令的资源加载器：锁定当前 Submission 防止重复重建。"""
    return db.query(ResumeSubmission).filter(ResumeSubmission.resume_submission_id == submission_id).with_for_update().one_or_none()


def _command_key(
    provided: str | None, *, user: User, action: str, resource_id: str, body: dict
) -> str:
    """为未显式传入幂等键的请求生成字段上限内的稳定兜底键。"""
    if provided:
        return provided
    identity = {
        "action": action,
        "user_id": user.user_id,
        "resource_id": resource_id,
        "body": body,
    }
    digest = hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    # 不拼接 action/resource 的原文，避免长动作名或未来更长的业务 ID 超过
    # ``idempotency_keys.idempotency_key`` 的 varchar(128) 约束。
    return f"legacy:v1:{digest}"

def _reparse_response(result: tuple[ResumeSubmission, WorkflowRun]) -> dict:
    submission, run = result
    return {
        "submission_id": submission.resume_submission_id,
        "workflow_run_id": run.workflow_run_id,
        "status": submission.status,
    }


def _reparse_command(
    *, db: Session, user: User, submission_id: str, idempotency_key: str | None,
) -> tuple[ResumeSubmission, WorkflowRun]:
    """通过 CommandRunner 发起重新解析，并在幂等重放时回读持久化资源。"""
    body: dict = {}
    response = CommandRunner(db).execute(
        spec=CommandSpec(
            action="candidate.resume.reparse",
            resource_type="resume_submission",
            permission_code=None,
            authorization_action="force_fresh_parse",
        ),
        user=user,
        resource_id=submission_id,
        body=body,
        idempotency_key=_command_key(
            idempotency_key, user=user, action="candidate.resume.reparse",
            resource_id=submission_id, body=body,
        ),
        resource_loader=_load_resume_submission,
        handler=lambda context: _reparse_response(
            CandidateResumeRebuildService(db).request_reparse(
                submission=context.resource, user=context.user,
            )
        ),
    )
    submission = db.get(ResumeSubmission, str(response["submission_id"]))
    run = db.get(WorkflowRun, str(response["workflow_run_id"]))
    if submission is None or run is None:
        raise RuntimeError("candidate_reparse_command_resource_missing")
    return submission, run



class IntakeCandidateDetailView(BaseModel):
    candidate_id: str
    display_name: str
    status: str
    major: str = ""
    resume_profile_id: str = ""


class DuplicateTargetView(BaseModel):
    """单一重复匹配真正指向的已有候选人，不是本次上传的占位档案。"""

    candidate_id: str
    display_name: str
    application_count: int = 0
    active_application_count: int = 0
    can_replace: bool = False


class IntakeDocumentDetailView(BaseModel):
    """当前 ResumeSubmission 关联文件的公开摘要。"""

    source_document_id: str
    filename: str
    available: bool

def _submission_result_response(result) -> dict:
    """把简历服务结果转换为可持久化、可幂等重放的 JSON 响应。"""
    if isinstance(result, tuple):
        submission = result[0]
        response = {"submission_id": submission.resume_submission_id}
        if len(result) > 1:
            if isinstance(result[1], list):
                response["application_ids"] = list(result[1])
            elif result[1] is not None:
                # CommandRunner 会把响应写入幂等账本，因此这里只能返回稳定的
                # JSON 值。直接保存 WorkflowRun ORM 对象会导致首次请求在提交后
                # 无法按 ID 回读，幂等重放时也会丢失该字段。
                next_run = result[1]
                response["next_workflow_run_id"] = (
                    next_run.workflow_run_id
                    if isinstance(next_run, WorkflowRun)
                    else str(next_run)
                )
        if len(result) > 2:
            response["application_ids"] = list(result[2] or [])
        return response
    return {
        "submission_id": result.resume_submission_id,
        "application_ids": list(
            (result.review_context_json or {}).get("duplicateDecisionApplicationIds", [])
        ),
    }


def _submission_command(
    *, db: Session, user: User, submission_id: str, idempotency_key: str | None,
    action: str, authorization_action: str, body: dict, handler,
) -> dict:
    """Run non-file resume commands and return a persisted, replay-safe response."""
    requirement = RESUME_SUBMISSION_ACTION_REQUIREMENTS.get(authorization_action)
    if requirement is None:
        raise RuntimeError(f"resume_submission_action_not_defined:{authorization_action}")
    def run(context) -> dict:
        return _submission_result_response(
            handler(context.resource, context.user, context.body)
        )
    return CommandRunner(db).execute(
        spec=CommandSpec(
            action=action,
            resource_type="resume_submission",
            permission_code=requirement.permission_code,
            authorization_action=authorization_action,
        ),
        user=user,
        resource_id=submission_id,
        body=body,
        idempotency_key=_command_key(
            idempotency_key, user=user, action=action, resource_id=submission_id, body=body
        ),
        resource_loader=_load_resume_submission,
        handler=run,
    )


class IntakeWorkflowDetailView(BaseModel):
    workflow_run_id: str
    status: str
    error_message: str | None = None


class IntakeProcessView(BaseModel):
    """简历处理页面使用的统一流程状态，不暴露 Worker 内部异常文本。"""

    workflowRunId: str
    workflowType: str
    processStatus: str
    currentStep: str = ""
    currentStepLabel: str = ""
    attemptCount: int = 0
    maxAttempts: int = 0
    pollCount: int = 0
    maxPollAttempts: int = 0
    nextAttemptAt: str = ""
    publicMessage: str = ""
    recoveryAction: str = ""
    recoveryActionLabel: str = ""
    updatedAt: str = ""


class IntakeApplicationPrimaryActionView(BaseModel):
    type: Literal["navigate", "view_hard_screening_result"]
    label: str
    route: str | None = None


class IntakeApplicationView(BaseModel):
    """简历处理页中，Candidate 关联的一个岗位申请摘要。"""

    application_id: str
    job_id: str
    job_title: str
    department_id: str
    department_name: str = ""
    status: str
    rejection_stage: str | None = None
    main_route: str = "/candidates"
    primary_action: IntakeApplicationPrimaryActionView
    submitted_at: datetime
    adopted_resume_submission_id: str | None = None
    uses_current_resume: bool = False


class CandidateIntakeListItemView(BaseModel):
    submission_id: str
    candidate_id: str | None = None
    candidate_name: str
    candidate_status: str | None = None
    candidate_major: str = ""
    resume_profile_id: str = ""
    candidate_documents_available: bool = False
    filename: str
    resume_pdf_url: str | None = None
    bucket: Literal["processing", "review_required", "failed", "completed"]
    stage: str
    submission_status: ResumeSubmissionStatus
    # 以下四项是状态机的公开合同；payload 仅保留内部扩展和审计信息。
    intake_mode: Literal["initial", "reparse", "replacement", "manual_correction"] = "initial"
    review_kind: str | None = None
    failure_kind: str | None = None
    recovery_code: str | None = None
    is_current: bool = False
    superseded_by_submission_id: str | None = None
    recovery: dict = Field(default_factory=dict)
    processing_quality: dict = Field(default_factory=dict)
    # 这里只表示数据库中的文件引用完整；对象本体在用户读取时由文件接口校验。
    source_available: bool
    workflow_status: str | None = None
    workflow_run_id: str | None = None
    process: IntakeProcessView | None = None
    applications: list[IntakeApplicationView] = Field(default_factory=list)
    application_count: int = 0
    routing_status: str = "idle"
    routing_reason: str | None = None
    review_reason: str | None = None
    available_actions: list[RecoveryActionView] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class CandidateIntakeCountsView(BaseModel):
    processing_count: int = 0
    attention_count: int = 0
    completed_count: int = 0
    total_count: int = 0
    unread_attention_count: int = 0


class CandidateIntakeListView(BaseModel):
    items: list[CandidateIntakeListItemView] = Field(default_factory=list)
    total: int
    page: int
    page_size: int
    counts: CandidateIntakeCountsView


class CandidateIntakeReadView(BaseModel):
    last_read_at: datetime


class CandidateIntakeUploadResponse(BaseModel):
    document_id: str
    submission_id: str
    workflow_run_id: str
    status: ResumeSubmissionStatus
    reused: bool = False
    candidate_id: str | None = None
    application_id: str | None = None
    application_ids: list[str] = Field(default_factory=list)


class CandidateIntakeView(BaseModel):
    submission_id: str
    submission_status: ResumeSubmissionStatus
    intake_mode: Literal["initial", "reparse", "replacement", "manual_correction"] = "initial"
    review_kind: str | None = None
    failure_kind: str | None = None
    recovery_code: str | None = None
    duplicate_target: DuplicateTargetView | None = None
    is_current: bool = False
    superseded_by_submission_id: str | None = None
    recovery: dict = Field(default_factory=dict)
    processing_quality: dict = Field(default_factory=dict)
    candidate: IntakeCandidateDetailView | None = None
    document: IntakeDocumentDetailView | None = None
    workflow: IntakeWorkflowDetailView | None = None
    process: IntakeProcessView | None = None
    routing: dict = Field(default_factory=dict)
    routing_status: str = "idle"
    routing_reason: str | None = None
    applications: list[IntakeApplicationView] = Field(default_factory=list)
    review_reason: str | None = None
    available_actions: list[RecoveryActionView] = Field(default_factory=list)


class ResumeDuplicateDecisionRequest(BaseModel):
    decision: Literal["replace_resume", "discard_submission"]


class ResumeDuplicateDecisionResponse(BaseModel):
    submission_id: str
    status: ResumeSubmissionStatus
    application_ids: list[str] = Field(default_factory=list)


class DuplicateCandidateOptionView(BaseModel):
    candidate_id: str
    display_name: str
    school: str = ""
    major: str = ""
    application_count: int = 0
    active_application_count: int = 0
    can_replace: bool = False


class AmbiguousDuplicateOptionsResponse(BaseModel):
    submission_id: str
    candidates: list[DuplicateCandidateOptionView] = Field(default_factory=list)


class AmbiguousDuplicateResolutionRequest(BaseModel):
    decision: Literal["merge_into_candidate", "create_new_candidate", "discard_submission"]
    target_candidate_id: str | None = Field(default=None, max_length=64)


class AmbiguousDuplicateResolutionResponse(BaseModel):
    submission_id: str
    status: ResumeSubmissionStatus
    next_workflow_run_id: str | None = None
    application_ids: list[str] = Field(default_factory=list)

class ResumeStructureConfirmationResponse(BaseModel):
    """废弃接口的历史响应形状；当前确认命令固定返回 409，不会产生成功响应。"""

    submission_id: str
    status: ResumeSubmissionStatus
    next_workflow_run_id: str | None = None
    application_ids: list[str] = Field(default_factory=list)


class ManualRoutingJobOption(BaseModel):
    job_id: str
    jd_version_id: str
    job_profile_id: str | None = None
    title: str
    department_id: str
    department_name: str = ""
    job_status: Literal["setup_pending", "open"]
    profile_status: Literal[
        "queued", "processing", "ready", "review_required", "failed", "not_started"
    ] = "not_started"
    is_screening_ready: bool = False
    selection_outcome: Literal["start_screening", "wait_job_profile"]


class ManualRoutingOptionsResponse(BaseModel):
    candidate_id: str
    candidate_name: str
    candidate_major: str = ""
    jobs: list[ManualRoutingJobOption] = Field(default_factory=list)

class ManualApplicationCreateRequest(BaseModel):
    job_ids: list[str] = Field(min_length=1, max_length=100)


class ManualApplicationCreateResponse(BaseModel):
    candidate_id: str
    application_ids: list[str]


class ResumeCorrectionSourceBlock(BaseModel):
    block_id: str
    text: str
    source_line_start: int | None = None
    source_line_end: int | None = None


class ResumeCorrectionStructuredContext(BaseModel):
    """Readable project context; source refs are opaque correction handles."""

    context_id: str = ""
    context_type: str = "other_context"
    text: str = ""
    source_refs: list[dict] = Field(default_factory=list)


class ResumeCorrectionStructuredBullet(BaseModel):
    """A source-backed responsibility/result shown before WorkUnit extraction."""

    source_bullet_id: str = ""
    text: str = ""
    source_refs: list[dict] = Field(default_factory=list)


class ResumeCorrectionStructuredExperience(BaseModel):
    """One user-facing experience group, excluding scoring-only WorkUnits."""

    experience_unit_id: str = ""
    title: str = ""
    context_items: list[ResumeCorrectionStructuredContext] = Field(default_factory=list)
    source_bullets: list[ResumeCorrectionStructuredBullet] = Field(default_factory=list)
    title_source_refs: list[dict] = Field(default_factory=list)
    context_source_refs: list[dict] = Field(default_factory=list)
    work_source_refs: list[dict] = Field(default_factory=list)


class ResumeCorrectionDraftView(BaseModel):
    submission_id: str
    source_blocks: list[ResumeCorrectionSourceBlock] = Field(default_factory=list)
    candidate_facts: dict = Field(default_factory=dict)
    experience_units: list[ResumeCorrectionStructuredExperience] = Field(default_factory=list)
    skill_claims: list[dict] = Field(default_factory=list)


class ResumeManualCorrectionRequest(BaseModel):
    """Editable fields are values plus IDs of immutable source blocks only."""

    model_config = ConfigDict(extra="forbid")

    candidate_facts: dict = Field(default_factory=dict)
    experience_units: list[dict] = Field(default_factory=list)
    skill_claims: list[dict] = Field(default_factory=list)

class CandidateResumeRebuildView(BaseModel):
    submission_id: str
    workflow_run_id: str
    status: ResumeSubmissionStatus
    mode: Literal[
        "reparse", "replacement", "manual_correction",
        "publish_retry", "routing_retry",
    ]


@router.get("", response_model=CandidateIntakeListView)
def list_candidate_intakes(
    status: Literal["all", "processing", "review_required", "failed", "completed"] = "all",
    keyword: str = Query(default="", max_length=128),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return CandidateIntakeQueryService(db).list(
        user=user, status=status, keyword=keyword, page=page, page_size=page_size
    )


@router.post(
    "/uploads",
    response_model=CandidateIntakeUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_candidate_resume(
    file: UploadFile = File(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    data = await file.read(settings.document_max_file_size + 1)
    filename = file.filename or "resume.pdf"
    content_type = file.content_type
    source_sha256 = hashlib.sha256(data).hexdigest()

    def handler(context) -> dict:
        service = ResumeDocumentService(db)
        document, submission, run, reused = service.uploads.upload_resume_document(
            user=context.user,
            filename=filename,
            content_type=content_type,
            data=data,

        )
        if not reused:
            context.compensate_on_rollback(
                lambda object_ref=document.object_ref: service.uploads.store.delete_object(object_ref)
            )
        return {
            "document_id": document.source_document_id,
            "submission_id": submission.resume_submission_id,
            "workflow_run_id": run.workflow_run_id,
            "status": submission.status,
            "reused": reused,
            "candidate_id": submission.candidate_id,
            "application_id": submission.application_id,
            "application_ids": [],
        }

    return CandidateIntakeUploadResponse(
        **CommandRunner(db).execute(
            spec=CommandSpec(
                action="candidate.resume.upload",
                resource_type="upload",
                permission_code="resume.upload",
                # 未升级客户端仍由文件哈希重复检测保护。
                idempotent=bool(idempotency_key),
            ),
            user=user,
            resource_id=source_sha256,
            body={"filename": filename, "content_type": content_type or "", "sha256": source_sha256},
            idempotency_key=idempotency_key or f"legacy:candidate.resume.upload:{user.user_id}:{source_sha256}",
            handler=handler,
        )
    )


@router.get("/summary", response_model=CandidateIntakeCountsView)
def candidate_intake_summary(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    return CandidateIntakeQueryService(db).summary(user=user)


@router.post("/read", response_model=CandidateIntakeReadView)
def mark_candidate_intakes_read(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    return CommandRunner(db).execute(
        spec=CommandSpec(
            action="candidate.intake.mark_read",
            resource_type="system",
            permission_code=None,
            authorization_mode="authenticated_self",
            idempotent=False,
            audit_exempt=True,
        ),
        user=user,
        resource_id=user.user_id,
        body={},
        handler=lambda context: {
            "last_read_at": CandidateIntakeQueryService(db).mark_read(
                user=context.user
            )
        },
    )


@router.get("/{submission_id}", response_model=CandidateIntakeView)
def get_candidate_intake(
    submission_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    submission = ResumeDocumentService(db).get_submission(submission_id, user)
    return ResumeDocumentService(db).intake_view(submission_id, user)


@router.get("/{submission_id}/correction-draft", response_model=ResumeCorrectionDraftView)
def get_resume_correction_draft(
    submission_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    submission = ResumeDocumentService(db).get_submission(submission_id, user)
    return ResumeCorrectionDraftView(
        **CandidateResumeRebuildService(db).correction_draft(submission=submission, user=user)
    )


@router.post(
    "/{submission_id}/manual-correction",
    response_model=CandidateResumeRebuildView,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_resume_manual_correction(
    submission_id: str,
    body: ResumeManualCorrectionRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response = CommandRunner(db).execute(
        spec=CommandSpec(
            action="candidate.resume.manual_correction",
            resource_type="resume_submission",
            permission_code="resume_submission.manage",
            authorization_action="correct_parsed_resume",
        ),
        user=user,
        resource_id=submission_id,
        body=body.model_dump(),
        idempotency_key=_command_key(
            idempotency_key,
            user=user,
            action="candidate.resume.manual_correction",
            resource_id=submission_id,
            body=body.model_dump(),
        ),
        resource_loader=_load_resume_submission,
        handler=lambda context: _reparse_response(
            CandidateResumeRebuildService(db).request_manual_correction(
                submission=context.resource,
                user=context.user,
                correction=context.body,
            )
        ),
    )
    return CandidateResumeRebuildView(
        submission_id=str(response["submission_id"]),
        workflow_run_id=str(response["workflow_run_id"]),
        status=ResumeSubmissionStatus(str(response["status"])),
        mode="manual_correction",
    )

@router.get("/{submission_id}/workflow-timeline")
def get_candidate_intake_workflow_timeline(
    submission_id: str,
    limit: int = Query(default=20, ge=1, le=50),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """返回该简历任务的简版执行轨迹；先复用简历详情权限校验。"""
    ResumeDocumentService(db).get_submission(submission_id, user)
    return {
        "items": WorkflowExecutionTimelineQuery(db).for_resume_submission(
            submission_id=submission_id, limit=limit
        )
    }


@router.get("/{submission_id}/duplicate-candidates", response_model=AmbiguousDuplicateOptionsResponse)
def get_ambiguous_duplicate_candidates(
    submission_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return ResumeDocumentService(db).duplicate_candidate_options(submission_id, user)


@router.post("/{submission_id}/duplicate-resolution", response_model=AmbiguousDuplicateResolutionResponse)
def resolve_ambiguous_duplicate(
    submission_id: str,
    body: AmbiguousDuplicateResolutionRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response = _submission_command(
        db=db, user=user, submission_id=submission_id, idempotency_key=idempotency_key,
        action="candidate.resume.duplicate_resolution",
        authorization_action=(
            "discard_submission"
            if body.decision == "discard_submission"
            else "resolve_duplicate_candidates"
        ),
        body=body.model_dump(),
        handler=lambda resource, actor, payload: ResumeDocumentService(db).resolve_ambiguous_duplicate(submission_id, actor, decision=payload["decision"], target_candidate_id=payload.get("target_candidate_id")),
    )
    submission = db.get(ResumeSubmission, str(response["submission_id"]))
    if submission is None:
        raise RuntimeError("candidate_duplicate_resolution_resource_missing")
    next_workflow_id = response.get("next_workflow_run_id")
    application_ids = list(response.get("application_ids") or [])
    return AmbiguousDuplicateResolutionResponse(
        submission_id=submission.resume_submission_id,
        status=submission.status,
        next_workflow_run_id=next_workflow_id,
        application_ids=application_ids,
    )

@router.post("/{submission_id}/duplicate-decision", response_model=ResumeDuplicateDecisionResponse)
def decide_resume_duplicate(
    submission_id: str,
    body: ResumeDuplicateDecisionRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response = _submission_command(
        db=db,
        user=user,
        submission_id=submission_id,
        idempotency_key=idempotency_key,
        action="candidate.resume.duplicate_decision",
        authorization_action=(
            "replace_duplicate_resume"
            if body.decision == "replace_resume"
            else "discard_submission"
        ),
        body=body.model_dump(),
        handler=lambda resource, actor, payload: ResumeDocumentService(db).decide_duplicate(submission_id, actor, payload["decision"]),
    )
    submission = db.get(ResumeSubmission, str(response["submission_id"]))
    if submission is None:
        raise RuntimeError("candidate_duplicate_decision_resource_missing")
    return ResumeDuplicateDecisionResponse(
        submission_id=submission.resume_submission_id,
        status=submission.status,
        application_ids=list((submission.review_context_json or {}).get("duplicateDecisionApplicationIds", [])),
    )


@router.post(
    "/{submission_id}/confirm-structure",
    response_model=ResumeStructureConfirmationResponse,
)
def confirm_resume_structure(
    submission_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response = _submission_command(
        db=db,
        user=user,
        submission_id=submission_id,
        idempotency_key=idempotency_key,
        action="candidate.resume.confirm_structure",
        authorization_action="confirm_structure",
        body={},
        handler=lambda resource, actor, payload: ResumeDocumentService(db).confirm_structure(submission_id, actor),
    )
    submission = db.get(ResumeSubmission, str(response["submission_id"]))
    if submission is None:
        raise RuntimeError("candidate_structure_confirmation_resource_missing")
    next_workflow_id = response.get("next_workflow_run_id")
    application_ids = list(response.get("application_ids") or [])
    return ResumeStructureConfirmationResponse(
        submission_id=submission.resume_submission_id,
        status=submission.status,
        next_workflow_run_id=next_workflow_id,
        application_ids=application_ids,
    )


@router.get("/{submission_id}/routing-options", response_model=ManualRoutingOptionsResponse)
def get_manual_routing_options(
    submission_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return ResumeDocumentService(db).manual_routing_options(submission_id, user)

@router.post("/{submission_id}/applications", response_model=ManualApplicationCreateResponse)
def manually_create_applications(
    submission_id: str,
    body: ManualApplicationCreateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response = _submission_command(
        db=db,
        user=user,
        submission_id=submission_id,
        idempotency_key=idempotency_key,
        action="candidate.resume.manual_routing",
        authorization_action="create_applications",
        body=body.model_dump(),
        handler=lambda resource, actor, payload: ResumeDocumentService(db).manually_create_applications(submission_id, actor, payload["job_ids"]),
    )
    submission = db.get(ResumeSubmission, str(response["submission_id"]))
    if submission is None:
        raise RuntimeError("candidate_manual_routing_resource_missing")
    return ManualApplicationCreateResponse(
        candidate_id=str(submission.candidate_id),
        application_ids=list(response.get("application_ids") or []),
    )


@router.post(
    "/{submission_id}/retry",
    response_model=CandidateIntakeUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_candidate_intake(
    submission_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = ResumeDocumentService(db)
    submission = service.get_submission(submission_id, user)
    if submission.status != ResumeSubmissionStatus.FAILED.value:
        # 待确认必须走对应的确认命令；重试只从技术失败创建一个新的简历版本。
        from backend.app.shared.errors import BusinessRuleError
        raise BusinessRuleError(status_code=409, detail="只有失败的简历任务可以重试；待确认请使用对应确认操作")
    next_submission, run = _reparse_command(db=db, user=user, submission_id=submission_id, idempotency_key=idempotency_key)
    document = service.get_document_for_submission(next_submission)
    return CandidateIntakeUploadResponse(
        document_id=document.source_document_id,
        submission_id=next_submission.resume_submission_id,
        workflow_run_id=run.workflow_run_id,
        status=next_submission.status,
        reused=False,
        candidate_id=next_submission.candidate_id,
        application_id=next_submission.application_id,
    )


@router.post("/{submission_id}/rebuild", response_model=CandidateResumeRebuildView, status_code=status.HTTP_202_ACCEPTED)
def rebuild_candidate_from_resume(submission_id: str, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    next_submission, run = _reparse_command(db=db, user=user, submission_id=submission_id, idempotency_key=idempotency_key)
    return CandidateResumeRebuildView(submission_id=next_submission.resume_submission_id, workflow_run_id=run.workflow_run_id, status=next_submission.status, mode="reparse")


@router.post("/{submission_id}/retry-publish", response_model=CandidateResumeRebuildView, status_code=status.HTTP_202_ACCEPTED)
def retry_candidate_resume_publish(
    submission_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # 发布重试是用户命令，必须和重新解析/人工校正一样经过统一的锁、授权和幂等层。
    body: dict = {}
    response = _submission_command(
        db=db,
        user=user,
        submission_id=submission_id,
        idempotency_key=idempotency_key,
        action="candidate.resume.retry_publish",
        authorization_action="retry_publish_resume",
        body=body,
        handler=lambda resource, actor, _payload: CandidateResumeRebuildService(db).request_publish_retry(
            submission=resource,
            user=actor,
        ),
    )
    next_submission = db.get(ResumeSubmission, str(response["submission_id"]))
    if next_submission is None:
        raise RuntimeError("candidate_publish_retry_command_resource_missing")
    run = db.get(WorkflowRun, str(response.get("next_workflow_run_id") or ""))
    if run is None:
        raise RuntimeError("candidate_publish_retry_command_workflow_missing")
    return CandidateResumeRebuildView(
        submission_id=next_submission.resume_submission_id,
        workflow_run_id=run.workflow_run_id,
        status=next_submission.status,
        mode="publish_retry",
    )


@router.post("/{submission_id}/retry-routing", response_model=CandidateResumeRebuildView, status_code=status.HTTP_202_ACCEPTED)
def retry_candidate_routing(
    submission_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    body: dict = {}
    response = _submission_command(
        db=db,
        user=user,
        submission_id=submission_id,
        idempotency_key=idempotency_key,
        action="candidate.resume.retry_routing",
        authorization_action="retry_routing",
        body=body,
        handler=lambda resource, actor, _payload: (
            resource,
            CandidateRoutingService(db).retry_submission(resource, user=actor),
        ),
    )
    next_submission = db.get(ResumeSubmission, str(response["submission_id"]))
    if next_submission is None:
        raise RuntimeError("candidate_routing_retry_command_resource_missing")
    run = db.get(WorkflowRun, str(response.get("next_workflow_run_id") or ""))
    if run is None:
        raise RuntimeError("candidate_routing_retry_command_workflow_missing")
    return CandidateResumeRebuildView(
        submission_id=next_submission.resume_submission_id,
        workflow_run_id=run.workflow_run_id,
        status=next_submission.status,
        mode="routing_retry",
    )


@router.post("/{submission_id}/replacement", response_model=CandidateResumeRebuildView, status_code=status.HTTP_202_ACCEPTED)
async def replace_candidate_resume(
    submission_id: str,
    file: UploadFile = File(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    data = await file.read(settings.document_max_file_size + 1)
    filename = file.filename or "resume.pdf"
    content_type = file.content_type
    source_sha256 = hashlib.sha256(data).hexdigest()

    def handler(context) -> dict:
        service = ResumeDocumentService(db)
        rebuild = CandidateResumeRebuildService(db)
        candidate = rebuild.prepare_replacement(
            submission=context.resource,
            user=context.user,
        )
        document, replacement, run, reused = service.uploads.upload_resume_document(
            user=context.user,
            filename=filename,
            content_type=content_type,
            data=data,
            candidate_id=candidate.candidate_id,
            rebuild_from_submission_id=context.resource.resume_submission_id,
            authorization_permission="resume_submission.manage",

        )
        if not reused:
            context.compensate_on_rollback(
                lambda object_ref=document.object_ref: service.uploads.store.delete_object(object_ref)
            )
        rebuild.register_replacement(
            previous=context.resource,
            replacement=replacement,
            user=context.user,

        )
        return {
            "submission_id": replacement.resume_submission_id,
            "workflow_run_id": run.workflow_run_id,
            "status": replacement.status,
            "mode": "replacement",
        }

    return CandidateResumeRebuildView(
        **CommandRunner(db).execute(
            spec=CommandSpec(
                action="candidate.resume.replacement",
                resource_type="resume_submission",
                permission_code="resume_submission.manage",
                authorization_action="upload_replacement_resume",
            ),
            user=user,
            resource_id=submission_id,
            body={"filename": filename, "content_type": content_type or "", "sha256": source_sha256},
            idempotency_key=idempotency_key or f"legacy:candidate.resume.replacement:{user.user_id}:{submission_id}:{source_sha256}",
            resource_loader=_load_resume_submission,
            handler=handler,
        )
    )
