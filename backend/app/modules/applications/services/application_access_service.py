"""Application 访问服务：集中校验申请可见性及关联简历文件访问。"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from backend.app.models.entities import Application, ResumeSubmission, SourceDocument, User
from backend.app.modules.auth.public import AuthorizationService
from backend.app.shared.errors import BusinessError
from backend.app.storage.object_store import ObjectStore


class ApplicationAccessService:
    """Loads application resources after candidate-view scope checks."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.store = ObjectStore()

    def get_visible(self, user: User, application_id: str) -> Application:
        app = self.db.get(Application, application_id)
        if app is None or app.deleted_at is not None:
            raise BusinessError(
                "application_not_found", "未找到候选申请", status_code=404
            )
        AuthorizationService(self.db).require_application_view(user, app)
        return app

    def resume_pdf_path(self, application_id: str) -> Path:
        app = self.db.get(Application, application_id)
        if app is None or app.deleted_at is not None:
            raise BusinessError(
                "application_not_found", "未找到候选申请", status_code=404
            )
        submission = (
            self.db.get(ResumeSubmission, app.adopted_resume_submission_id)
            if app.adopted_resume_submission_id else None
        )
        document = (
            self.db.get(SourceDocument, submission.source_document_id)
            if submission is not None else None
        )
        # 新申请只通过正式外键读取；旧记录尚未迁移前才回退到历史文件副本。
        object_ref = document.object_ref if document is not None else None
        if not object_ref:
            raise BusinessError(
                "resume_pdf_missing", "该申请未关联可用简历 PDF", status_code=404
            )
        try:
            path = self.store.materialize(
                str(object_ref), f"applications/{application_id}/resume.pdf"
            )
        except Exception as exc:
            raise BusinessError(
                "resume_pdf_read_failed", "读取简历 PDF 对象失败", status_code=502
            ) from exc
        if not path.exists() or not path.is_file():
            raise BusinessError(
                "resume_pdf_file_missing", "简历 PDF 文件不存在", status_code=404
            )
        return path
