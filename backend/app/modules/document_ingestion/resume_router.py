from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from backend.app.modules.auth.public import get_current_user
from backend.app.db.session import get_db
from backend.app.models.entities import SourceDocument, User
from backend.app.modules.document_ingestion.schemas.view_schemas import (
    JobOptionView,
    ResumeParsedContentView,
    ResumeSubmissionView,
)
from backend.app.modules.document_ingestion.services import ResumeDocumentService


router = APIRouter(tags=["resume-documents"])


@router.get("/jobs", response_model=list[JobOptionView])
def list_active_jobs(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return [JobOptionView(**item) for item in ResumeDocumentService(db).list_active_job_options(user)]


@router.get(
    "/resume-documents/{submission_id}",
    response_model=ResumeSubmissionView,
)
def get_resume_submission(
    submission_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return ResumeDocumentService(db).get_submission(submission_id, user)


@router.get(
    "/resume-documents/{submission_id}/parsed-content",
    response_model=ResumeParsedContentView,
)
def get_resume_parsed_content(
    submission_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = ResumeDocumentService(db)
    submission = service.get_submission(submission_id, user)
    # 源文件缺失本身是可恢复业务状态：不能在这里直接返回 404，否则前端看不到
    # “重新上传”入口。提交记录仍可读取，页面据 source_available 显示可执行恢复动作。
    document = db.get(SourceDocument, submission.source_document_id)
    source_available = bool(
        document is not None
        and document.document_type == "resume"
        and document.object_ref
    )
    return ResumeParsedContentView(
        submission_id=submission.resume_submission_id,
        filename=document.original_filename if source_available else "原始简历文件已缺失",
        submission_status=submission.status,
        source_available=source_available,
        parsed_text=submission.parsed_text or "",
        parse_result=dict(submission.parse_result_json or {}),
        error_message=submission.error_message or ("原始简历文件已缺失，请重新上传" if not source_available else None),
    )


@router.get("/resume-documents/{submission_id}/pdf")
def resume_pdf(
    submission_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = ResumeDocumentService(db)
    submission = service.get_submission(submission_id, user)
    path = service.resume_pdf_path(submission)
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=path.name,
        content_disposition_type="inline",
    )
