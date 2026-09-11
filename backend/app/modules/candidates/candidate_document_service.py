from __future__ import annotations

import hashlib
import re
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.models.entities import (
    Application,
    Candidate,
    CandidateDocumentLink,
    ResumeSubmission,
    SourceDocument,
    User,
)
from backend.app.modules.auth.public import AuthorizationService, can_business_action
from backend.app.shared.audit import record_audit_event
from backend.app.shared.errors import BusinessRuleError
from backend.app.storage.object_store import ObjectStore


CANDIDATE_DOCUMENT_CATEGORIES = {
    "psychological_assessment",
    "academic_transcript",
    "certificate",
    "portfolio",
    "other",
}
_locks_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12].upper()}"


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class CandidateDocumentService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.store = ObjectStore()
        self.auth = AuthorizationService(db)

    def _candidate(self, candidate_id: str) -> Candidate:
        candidate = self.db.get(Candidate, candidate_id)
        if candidate is None or candidate.status == "archived":
            raise BusinessRuleError(status_code=404, detail="未找到候选人")
        return candidate

    def _assert_view_scope(self, user: User, candidate_id: str) -> None:
        # Candidate 附件虽为候选人级共享资料，但上传和维护附件不会修改其他
        # Application 的流程状态。范围前置只要求任一申请可见，具体写权限与
        # 上传人限制继续由 _can_manage 统一判断。
        self.auth.require_candidate_material_view(user, candidate_id)

    def _can_manage(self, user: User, candidate_id: str, uploaded_by: str | None = None) -> bool:
        applications = list(self.db.scalars(select(Application).where(Application.candidate_id == candidate_id, Application.deleted_at.is_(None))))
        if any(can_business_action(self.db, user, "candidate_document.manage", department_id=app.department_id) for app in applications):
            return True
        if uploaded_by == user.user_id:
            return any(can_business_action(self.db, user, "candidate_document.upload", department_id=app.department_id) for app in applications) or can_business_action(self.db, user, "candidate_document.upload", require_organization_scope=True)
        return False

    def list_view(self, user: User, candidate_id: str) -> dict[str, Any]:
        self._candidate(candidate_id)
        self._assert_view_scope(user, candidate_id)
        active_application_count = int(
            self.db.scalar(
                select(func.count())
                .select_from(Application)
                .where(
                    Application.candidate_id == candidate_id,
                    Application.deleted_at.is_(None),
                )
            )
            or 0
        )
        current_resume = None
        if self._candidate(candidate_id).current_resume_submission_id:
            submission = self.db.get(
                ResumeSubmission,
                self._candidate(candidate_id).current_resume_submission_id,
            )
            resume_document = self.db.get(SourceDocument, submission.source_document_id) if submission else None
            if (
                submission
                and resume_document
                and resume_document.document_type == "resume"
                and resume_document.object_ref
            ):
                current_resume = {
                    "submissionId": submission.resume_submission_id,
                    "filename": resume_document.original_filename,
                    "pdfUrl": f"/api/v1/resume-documents/{submission.resume_submission_id}/pdf",
                    "uploadedAt": resume_document.created_at.isoformat(),
                    "status": submission.status,
                }
        rows = self.db.execute(
            select(CandidateDocumentLink, SourceDocument, User)
            .join(SourceDocument, SourceDocument.source_document_id == CandidateDocumentLink.source_document_id)
            .join(User, User.user_id == CandidateDocumentLink.uploaded_by)
            .where(CandidateDocumentLink.candidate_id == candidate_id, CandidateDocumentLink.is_active.is_(True))
            .order_by(CandidateDocumentLink.sort_order, CandidateDocumentLink.created_at)
        ).all()
        documents = [self._view(candidate_id, link, document, uploader, user) for link, document, uploader in rows]
        can_upload = self._can_manage(user, candidate_id, user.user_id)
        return {
            "candidateId": candidate_id,
            "activeApplicationCount": active_application_count,
            "currentResume": current_resume,
            "documents": documents,
            "permissions": {"canUpload": can_upload},
        }

    def upload(self, *, user: User, candidate_id: str, display_name: str | None, category: str, source_stage: str, note: str | None, filename: str, content_type: str | None, data: bytes, created_object_refs: list[str] | None = None) -> dict[str, Any]:
        self._candidate(candidate_id)
        self._assert_view_scope(user, candidate_id)
        if not self._can_manage(user, candidate_id, user.user_id):
            raise BusinessRuleError(status_code=403, detail="无权上传候选人资料")
        clean_filename = Path(filename).name.strip() or "资料.pdf"
        self._validate_pdf(clean_filename, content_type, data)
        if category not in CANDIDATE_DOCUMENT_CATEGORIES:
            raise BusinessRuleError(status_code=422, detail="资料分类无效")
        clean_name = self._display_name(display_name, clean_filename)
        sha256 = hashlib.sha256(data).hexdigest()
        document = self.db.scalar(select(SourceDocument).where(SourceDocument.document_type == "candidate_attachment_pdf", SourceDocument.source_sha256 == sha256))
        if document is None:
            object_ref, stored_sha256 = self.store.put_bytes(f"documents/candidates/{candidate_id}/{sha256}.pdf", data, "application/pdf")
            if stored_sha256 != sha256:
                raise RuntimeError("stored_document_hash_mismatch")
            if created_object_refs is not None:
                created_object_refs.append(object_ref)
            document = SourceDocument(source_document_id=_id("DOC"), document_type="candidate_attachment_pdf", original_filename=clean_filename, content_type="application/pdf", object_ref=object_ref, source_sha256=sha256, parsed_text=None, external_document_id=None, uploaded_by=user.user_id, created_at=_now(), updated_at=_now())
            self.db.add(document)
            self.db.flush()
        link = self.db.scalar(select(CandidateDocumentLink).where(CandidateDocumentLink.candidate_id == candidate_id, CandidateDocumentLink.source_document_id == document.source_document_id))
        if link is None:
            link = CandidateDocumentLink(document_link_id=_id("CDL"), candidate_id=candidate_id, source_document_id=document.source_document_id, display_name=clean_name, category=category, source_stage=source_stage, note=(note or "").strip() or None, sort_order=self._next_order(candidate_id), is_active=True, uploaded_by=user.user_id, created_at=_now(), updated_at=_now())
            self.db.add(link)
        else:
            link.display_name, link.category, link.source_stage, link.note, link.is_active, link.updated_at = clean_name, category, source_stage, (note or "").strip() or None, True, _now()
        record_audit_event(self.db, actor=user, action="candidate_document.upload", target_type="candidate_document", target_id=link.document_link_id, summary=f"上传候选人资料：{clean_name}", details={"candidateId": candidate_id, "category": category})
        self.db.flush()
        return self._view(candidate_id, link, document, self.db.get(User, link.uploaded_by), user)

    def update(self, user: User, candidate_id: str, document_id: str, display_name: str) -> dict[str, Any]:
        link, document = self._link(candidate_id, document_id)
        self._assert_view_scope(user, candidate_id)
        if not self._can_manage(user, candidate_id, link.uploaded_by):
            raise BusinessRuleError(status_code=403, detail="无权修改候选人资料")
        link.display_name = self._display_name(display_name, document.original_filename)
        link.updated_at = _now()
        record_audit_event(self.db, actor=user, action="candidate_document.rename", target_type="candidate_document", target_id=document_id, summary=f"重命名候选人资料：{link.display_name}", details={"candidateId": candidate_id})
        self.db.flush()
        return self._view(candidate_id, link, document, self.db.get(User, link.uploaded_by), user)

    def delete(self, user: User, candidate_id: str, document_id: str) -> None:
        link, _ = self._link(candidate_id, document_id)
        self._assert_view_scope(user, candidate_id)
        if not self._can_manage(user, candidate_id, link.uploaded_by):
            raise BusinessRuleError(status_code=403, detail="无权删除候选人资料")
        link.is_active = False
        link.updated_at = _now()
        record_audit_event(self.db, actor=user, action="candidate_document.delete", target_type="candidate_document", target_id=document_id, summary=f"删除候选人资料：{link.display_name}", details={"candidateId": candidate_id})
        self.db.flush()

    def image_urls(self, user: User, candidate_id: str, document_id: str) -> dict[str, list[str]]:
        self._assert_view_scope(user, candidate_id)
        document = self._link(candidate_id, document_id)[1]
        pages = self._ensure_pages(candidate_id, document)
        return {"pages": [f"/api/v1/candidates/{candidate_id}/documents/{document_id}/pages/{page.name}" for page in pages]}

    def page_path(self, user: User, candidate_id: str, document_id: str, filename: str) -> Path:
        self._assert_view_scope(user, candidate_id)
        if not re.fullmatch(r"page-\d+\.png", filename):
            raise BusinessRuleError(status_code=400, detail="文档图片页文件名无效")
        document = self._link(candidate_id, document_id)[1]
        path = {page.name: page for page in self._ensure_pages(candidate_id, document)}.get(filename)
        if path is None:
            raise BusinessRuleError(status_code=404, detail="文档图片页不存在")
        return path

    def _link(self, candidate_id: str, document_id: str) -> tuple[CandidateDocumentLink, SourceDocument]:
        link = self.db.get(CandidateDocumentLink, document_id)
        if link is None or link.candidate_id != candidate_id or not link.is_active:
            raise BusinessRuleError(status_code=404, detail="未找到候选人资料")
        document = self.db.get(SourceDocument, link.source_document_id)
        if document is None:
            raise BusinessRuleError(status_code=404, detail="文件本体不存在")
        return link, document

    def _ensure_pages(self, candidate_id: str, document: SourceDocument) -> list[Path]:
        cache_key = document.source_document_id
        pdf_path = self.store.materialize(document.object_ref, f"documents/{cache_key}.pdf")
        cache_dir = Path(settings.local_object_store_dir) / "document_pages" / cache_key
        cache_dir.mkdir(parents=True, exist_ok=True)
        marker = cache_dir / ".complete"
        with _locks_guard:
            lock = _locks.setdefault(cache_key, threading.Lock())
        with lock:
            existing = sorted(cache_dir.glob("page-*.png"), key=lambda p: int(re.search(r"page-(\d+)", p.name).group(1)))
            if marker.exists() and existing:
                return existing
            import fitz
            pdf = fitz.open(pdf_path)
            try:
                generated = []
                for index, page in enumerate(pdf, 1):
                    target = cache_dir / f"page-{index}.png"
                    target.write_bytes(page.get_pixmap(dpi=144, alpha=False).tobytes("png"))
                    generated.append(target)
            finally:
                pdf.close()
            marker.write_text(str(len(generated)), encoding="utf-8")
            return generated

    def _view(self, candidate_id: str, link: CandidateDocumentLink, document: SourceDocument, uploader: User | None, user: User) -> dict[str, Any]:
        can_manage = self._can_manage(user, candidate_id, link.uploaded_by)
        return {"documentId": link.document_link_id, "scope": "candidate", "displayName": link.display_name, "filename": document.original_filename, "primary": False, "category": link.category if link.category in CANDIDATE_DOCUMENT_CATEGORIES else "other", "sourceStage": link.source_stage, "note": link.note, "status": "ready", "imagesUrl": f"/api/v1/candidates/{candidate_id}/documents/{link.document_link_id}/images", "canRename": can_manage, "canDelete": can_manage, "uploadedAt": document.created_at.isoformat(), "uploadedBy": link.uploaded_by, "uploadedByName": uploader.display_name if uploader else ""}

    def _next_order(self, candidate_id: str) -> int:
        return int(self.db.scalar(select(func.max(CandidateDocumentLink.sort_order)).where(CandidateDocumentLink.candidate_id == candidate_id)) or 0) + 1

    @staticmethod
    def _display_name(value: str | None, filename: str) -> str:
        name = (value or "").strip() or Path(filename).stem.strip() or "未命名资料"
        if len(name) > 128:
            raise BusinessRuleError(status_code=422, detail="资料名称不能超过128个字符")
        return name

    @staticmethod
    def _validate_pdf(filename: str, content_type: str | None, data: bytes) -> None:
        if not filename.lower().endswith(".pdf") or not data.startswith(b"%PDF-"):
            raise BusinessRuleError(status_code=400, detail="只支持有效的PDF文件")
        if content_type and content_type not in {"application/pdf", "application/octet-stream"}:
            raise BusinessRuleError(status_code=400, detail="文件类型不是PDF")
        if len(data) > settings.document_max_file_size:
            raise BusinessRuleError(status_code=413, detail="PDF文件超过大小限制")
