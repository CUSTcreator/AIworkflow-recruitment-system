from __future__ import annotations

import hashlib
import re
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.app.shared.errors import BusinessError, BusinessRuleError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.models.entities import (
    Application,
    ApplicationDocumentLink,
    CandidateDocumentLink,
    JobDocumentLink,
    ResumeSubmission,
    SourceDocument,
    User,
)
from backend.app.modules.applications.services.application_access_service import ApplicationAccessService
from backend.app.modules.auth.public import AuthorizationService, can_business_action
from backend.app.shared.audit import record_audit_event
from backend.app.storage.object_store import ObjectStore


PRIMARY_RESUME_ID = "primary-resume"
APPLICATION_DOCUMENT_CATEGORIES = {
    "standard_resume",
    "psychological_assessment",
    "academic_transcript",
    "other",
}
_render_locks_guard = threading.Lock()
_render_locks: dict[str, threading.Lock] = {}


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12].upper()}"


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _render_lock(key: str) -> threading.Lock:
    with _render_locks_guard:
        return _render_locks.setdefault(key, threading.Lock())


def _page_sort_key(path: Path) -> int:
    match = re.search(r"page-(\d+)\.png$", path.name)
    return int(match.group(1)) if match else 0


class ApplicationDocumentService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.access = ApplicationAccessService(db)
        self.store = ObjectStore()

    def list_view(self, user: User, application_id: str) -> dict[str, Any]:
        app = self.access.get_visible(user, application_id)
        documents: list[dict[str, Any]] = []
        primary = self._primary_resume(app)
        if primary is not None:
            documents.append(self._view(app, PRIMARY_RESUME_ID, "application", "候选人简历", primary, True, user))

        candidate_rows = self.db.execute(
            select(CandidateDocumentLink, SourceDocument)
            .join(SourceDocument, SourceDocument.source_document_id == CandidateDocumentLink.source_document_id)
            .where(
                CandidateDocumentLink.candidate_id == app.candidate_id,
                CandidateDocumentLink.is_active.is_(True),
            )
            .order_by(CandidateDocumentLink.sort_order, CandidateDocumentLink.created_at)
        ).all() if app.candidate_id else []
        for link, document in candidate_rows:
            documents.append(self._candidate_view(app, link, document, user))

        application_rows = self.db.execute(
            select(ApplicationDocumentLink, SourceDocument)
            .join(SourceDocument, SourceDocument.source_document_id == ApplicationDocumentLink.source_document_id)
            .where(
                ApplicationDocumentLink.application_id == application_id,
                ApplicationDocumentLink.is_active.is_(True),
            )
            .order_by(ApplicationDocumentLink.sort_order, ApplicationDocumentLink.created_at)
        ).all()
        for link, document in application_rows:
            documents.append(self._view(app, link.document_link_id, "application", link.display_name, document, False, user, link))

        job_rows = self.db.execute(
            select(JobDocumentLink, SourceDocument)
            .join(SourceDocument, SourceDocument.source_document_id == JobDocumentLink.source_document_id)
            .where(JobDocumentLink.job_id == app.job_id, JobDocumentLink.is_active.is_(True))
            .order_by(JobDocumentLink.sort_order, JobDocumentLink.created_at)
        ).all()
        for link, document in job_rows:
            documents.append(self._view(app, link.document_link_id, "job", link.display_name, document, False, user))

        return {
            "applicationId": application_id,
            "candidateId": app.candidate_id,
            "documents": documents,
            "permissions": {
                "canUpload": can_business_action(
                    self.db,
                    user,
                    "candidate_document.upload",
                    department_id=app.department_id,
                ) or can_business_action(self.db, user, "candidate_document.upload", require_organization_scope=True),
            },
        }

    def upload(
        self,
        *,
        user: User,
        application_id: str,
        display_name: str | None,
        category: str,
        source_stage: str,
        note: str | None,
        filename: str,
        content_type: str | None,
        data: bytes,
        created_object_refs: list[str] | None = None,
    ) -> dict[str, Any]:
        app = self.access.get_visible(user, application_id)
        if not can_business_action(
            self.db, user, "candidate_document.upload", department_id=app.department_id
        ):
            raise BusinessRuleError(status_code=403, detail="无权上传候选人文件")
        clean_filename = Path(filename).name.strip() or "资料.pdf"
        if len(clean_filename) > 255:
            raise BusinessRuleError(status_code=422, detail="文件名不能超过255个字符")
        self._validate_pdf(clean_filename, content_type, data)
        if category not in APPLICATION_DOCUMENT_CATEGORIES:
            raise BusinessRuleError(status_code=422, detail="资料分类无效")
        clean_name = self._display_name(display_name, clean_filename)

        sha256 = hashlib.sha256(data).hexdigest()
        document = self.db.scalar(
            select(SourceDocument).where(
                SourceDocument.document_type == "application_attachment_pdf",
                SourceDocument.source_sha256 == sha256,
            )
        )
        if document is None:
            document_id = _id("DOC")
            object_ref, stored_sha256 = self.store.put_bytes(
                f"documents/applications/{sha256}.pdf", data, "application/pdf"
            )
            if stored_sha256 != sha256:
                raise RuntimeError("stored_document_hash_mismatch")
            if created_object_refs is not None:
                created_object_refs.append(object_ref)
            document = SourceDocument(
                source_document_id=document_id,
                document_type="application_attachment_pdf",
                original_filename=clean_filename,
                content_type="application/pdf",
                object_ref=object_ref,
                source_sha256=sha256,
                parsed_text=None,
                external_document_id=None,
                uploaded_by=user.user_id,
                created_at=_now(),
                updated_at=_now(),
            )
            self.db.add(document)
            self.db.flush()

        link = self.db.scalar(
            select(ApplicationDocumentLink).where(
                ApplicationDocumentLink.application_id == application_id,
                ApplicationDocumentLink.source_document_id == document.source_document_id,
            )
        )
        if link is None:
            link = ApplicationDocumentLink(
                document_link_id=_id("ADL"),
                application_id=application_id,
                source_document_id=document.source_document_id,
                display_name=clean_name,
                category=category,
                source_stage=source_stage,
                note=(note or "").strip() or None,
                sort_order=self._next_order(ApplicationDocumentLink, "application_id", application_id),
                is_active=True,
                uploaded_by=user.user_id,
                created_at=_now(),
                updated_at=_now(),
            )
            self.db.add(link)
        else:
            link.display_name = clean_name
            link.category = category
            link.source_stage = source_stage
            link.note = (note or "").strip() or None
            link.is_active = True
            link.updated_at = _now()
        record_audit_event(
            self.db,
            actor=user,
            action="candidate_document.upload",
            target_type="application_document",
            target_id=link.document_link_id,
            summary=f"上传候选人文件：{clean_name}",
            details={"applicationId": application_id, "category": category, "sourceStage": source_stage},
        )
        # 上传产生的对象存储补偿由外层命令注册；数据库统一由 CommandRunner 提交。
        self.db.flush()
        return self._view(app, link.document_link_id, "application", link.display_name, document, False, user, link)

    def update(
        self, user: User, application_id: str, document_id: str, display_name: str,
    ) -> dict[str, Any]:
        app = self.access.get_visible(user, application_id)
        link, document = self._application_document(app, document_id)
        self._assert_manage(user, app, link)
        link.display_name = self._display_name(display_name, document.original_filename)
        link.updated_at = _now()
        record_audit_event(self.db, actor=user, action="candidate_document.rename", target_type="application_document", target_id=document_id, summary=f"重命名候选人文件：{link.display_name}", details={"applicationId": application_id})
        self.db.flush()
        return self._view(app, document_id, "application", link.display_name, document, False, user, link)

    def delete(
        self, user: User, application_id: str, document_id: str
    ) -> None:
        app = self.access.get_visible(user, application_id)
        link, _ = self._application_document(app, document_id)
        self._assert_manage(user, app, link)
        link.is_active = False
        link.updated_at = _now()
        record_audit_event(self.db, actor=user, action="candidate_document.delete", target_type="application_document", target_id=document_id, summary=f"删除候选人文件：{link.display_name}", details={"applicationId": application_id})
        self.db.flush()

    def image_urls(self, user: User, application_id: str, document_id: str) -> dict[str, list[str]]:
        app = self.access.get_visible(user, application_id)
        self._require_document_material_view(user, app, document_id)
        pages = self._ensure_page_images(application_id, document_id)
        return {
            "pages": [
                f"/api/v1/applications/{application_id}/documents/{document_id}/pages/{page.name}"
                for page in pages
            ]
        }

    def page_path(self, user: User, application_id: str, document_id: str, filename: str) -> Path:
        app = self.access.get_visible(user, application_id)
        self._require_document_material_view(user, app, document_id)
        if not re.fullmatch(r"page-\d+\.png", filename):
            raise BusinessRuleError(status_code=400, detail="文档图片页文件名无效")
        pages = self._ensure_page_images(application_id, document_id)
        path = {page.name: page for page in pages}.get(filename)
        if path is None:
            raise BusinessRuleError(status_code=404, detail="文档图片页不存在")
        return path

    def _ensure_page_images(self, application_id: str, document_id: str) -> list[Path]:
        app = self.db.get(Application, application_id)
        if app is None:
            raise BusinessRuleError(status_code=404, detail="未找到候选申请")
        document = self._resolve_document(app, document_id)
        cache_key = document.source_document_id or f"resume-{application_id}"
        pdf_path = self.store.materialize(document.object_ref, f"documents/{cache_key}.pdf")
        if not pdf_path.is_file():
            raise BusinessRuleError(status_code=404, detail="PDF文件不存在")
        cache_dir = Path(settings.local_object_store_dir) / "document_pages" / cache_key
        cache_dir.mkdir(parents=True, exist_ok=True)
        marker = cache_dir / ".complete"
        with _render_lock(cache_key):
            existing = sorted(cache_dir.glob("page-*.png"), key=_page_sort_key)
            if marker.exists() and existing:
                return existing
            for page in existing:
                page.unlink(missing_ok=True)
            marker.unlink(missing_ok=True)
            generated: list[Path] = []
            try:
                import fitz

                pdf = fitz.open(pdf_path)
                try:
                    if pdf.page_count <= 0:
                        raise RuntimeError("PDF没有可渲染页面")
                    for index, page in enumerate(pdf, start=1):
                        target = cache_dir / f"page-{index}.png"
                        temporary = cache_dir / f".{target.name}-{uuid.uuid4().hex}.tmp"
                        temporary.write_bytes(page.get_pixmap(dpi=144, alpha=False).tobytes("png"))
                        temporary.replace(target)
                        generated.append(target)
                finally:
                    pdf.close()
                marker.write_text(str(len(generated)), encoding="utf-8")
            except Exception as exc:
                for page in generated:
                    page.unlink(missing_ok=True)
                raise BusinessRuleError(status_code=500, detail=f"PDF转图片失败：{type(exc).__name__}") from exc
            return generated

    def _resolve_document(self, app: Application, document_id: str) -> SourceDocument:
        if document_id == PRIMARY_RESUME_ID:
            document = self._primary_resume(app)
            if document is None:
                raise BusinessRuleError(status_code=404, detail="该申请未上传简历PDF")
            return document
        _, _, document = self._linked_document(app, document_id)
        return document

    def _require_document_material_view(
        self, user: User, app: Application, document_id: str
    ) -> None:
        """候选人简历与附件的二进制内容不应只依赖列表页隐藏。"""
        if document_id == PRIMARY_RESUME_ID:
            AuthorizationService(self.db).require_application_material_view(user, app)
            return
        scope, _, _ = self._linked_document(app, document_id)
        if scope == "application":
            AuthorizationService(self.db).require_application_material_view(user, app)

    def _linked_document(self, app: Application, document_id: str):
        application_link = self.db.get(ApplicationDocumentLink, document_id)
        if application_link is not None and application_link.application_id == app.application_id and application_link.is_active:
            document = self.db.get(SourceDocument, application_link.source_document_id)
            if document is not None:
                return "application", application_link, document
        job_link = self.db.get(JobDocumentLink, document_id)
        if job_link is not None and job_link.job_id == app.job_id and job_link.is_active:
            document = self.db.get(SourceDocument, job_link.source_document_id)
            if document is not None:
                return "job", job_link, document
        raise BusinessRuleError(status_code=404, detail="未找到申请文件")

    def _application_document(self, app: Application, document_id: str):
        link = self.db.get(ApplicationDocumentLink, document_id)
        if link is None or link.application_id != app.application_id or not link.is_active:
            candidate_link = self.db.get(CandidateDocumentLink, document_id)
            if candidate_link is not None and candidate_link.candidate_id == app.candidate_id and candidate_link.is_active:
                raise BusinessRuleError(
                    status_code=409,
                    detail="该资料属于候选人档案，请在候选人资料页面管理",
                )
            raise BusinessRuleError(status_code=404, detail="未找到申请文件")
        document = self.db.get(SourceDocument, link.source_document_id)
        if document is None:
            raise BusinessRuleError(status_code=404, detail="文件本体不存在")
        return link, document

    def _primary_resume(self, app: Application) -> SourceDocument | None:
        """返回该申请实际采用的简历文件。

        Application.adopted_resume_submission_id 是岗位申请的简历版本边界；
        因此文档工作台不能读取 Candidate 当前正在处理的新版本。旧 payload
        指针只用于迁移前历史数据的兼容回退。
        """
        submission = (
            self.db.get(ResumeSubmission, app.adopted_resume_submission_id)
            if app.adopted_resume_submission_id
            else None
        )
        if submission is not None:
            document = self.db.get(SourceDocument, submission.source_document_id)
            if document is not None:
                return document
        # 新申请只能通过 adopted_resume_submission_id 定位文件。
        return None

    def _view(self, app: Application, document_id: str, scope: str, name: str, document: SourceDocument, primary: bool, user: User, link: ApplicationDocumentLink | None = None) -> dict[str, Any]:
        can_manage = bool(link and self._can_manage(user, app, link))
        uploader = self.db.get(User, link.uploaded_by if link else document.uploaded_by)
        return {
            "documentId": document_id,
            "scope": scope,
            "displayName": name,
            "filename": document.original_filename,
            "primary": primary,
            "category": (
                "resume"
                if primary
                else self._visible_category(link.category)
                if link
                else "job"
            ),
            "sourceStage": "import" if primary else link.source_stage if link else "job",
            "note": link.note if link else None,
            # SourceDocument 行和对象引用存在即代表文件资产可用；处理进度归业务任务。
            "status": "ready",
            "imagesUrl": f"/api/v1/applications/{app.application_id}/documents/{document_id}/images",
            "canRename": can_manage,
            "canDelete": can_manage,
            "uploadedAt": document.created_at.isoformat(),
            "uploadedBy": (link.uploaded_by if link else document.uploaded_by),
            "uploadedByName": uploader.display_name if uploader else "",
        }

    def _candidate_view(self, app: Application, link: CandidateDocumentLink, document: SourceDocument, user: User) -> dict[str, Any]:
        can_manage = False
        try:
            AuthorizationService(self.db).require_candidate_material_view(user, app.candidate_id)
            can_manage = can_business_action(self.db, user, "candidate_document.manage", department_id=app.department_id) or (
                link.uploaded_by == user.user_id and can_business_action(self.db, user, "candidate_document.upload", department_id=app.department_id)
            )
        except BusinessError:
            can_manage = False
        active_application_count = int(
            self.db.scalar(
                select(func.count()).select_from(Application).where(
                    Application.candidate_id == app.candidate_id,
                    Application.deleted_at.is_(None),
                )
            )
            or 0
        )
        uploader = self.db.get(User, link.uploaded_by)
        return {
            "documentId": link.document_link_id,
            "scope": "candidate",
            "displayName": link.display_name,
            "filename": document.original_filename,
            "primary": False,
            "category": link.category if link.category in {"psychological_assessment", "academic_transcript", "certificate", "portfolio", "other"} else "other",
            "sourceStage": link.source_stage,
            "note": link.note,
            "status": "ready",
            "imagesUrl": f"/api/v1/candidates/{app.candidate_id}/documents/{link.document_link_id}/images",
            "canRename": can_manage,
            "canDelete": can_manage,
            "uploadedAt": document.created_at.isoformat(),
            "uploadedBy": link.uploaded_by,
            "uploadedByName": uploader.display_name if uploader else "",
            "deleteImpactCount": active_application_count,
        }

    def _assert_manage(self, user: User, app: Application, link: ApplicationDocumentLink) -> None:
        if not self._can_manage(user, app, link):
            raise BusinessRuleError(
                status_code=403,
                detail="需要候选人材料管理职责，或只能管理本人上传的候选人文件",
            )

    def _can_manage(self, user: User, app: Application, link: ApplicationDocumentLink) -> bool:
        # 材料管理职责可维护本部门可见申请的全部附件；只有上传职责的账号
        # 仍可维护本人上传的文件，不能借助候选人可见性操作他人的附件。
        if can_business_action(self.db, user, "candidate_document.manage", department_id=app.department_id):
            return True
        return link.uploaded_by == user.user_id and can_business_action(
            self.db, user, "candidate_document.upload", department_id=app.department_id
        )

    @staticmethod
    def _display_name(value: str | None, filename: str) -> str:
        name = (value or "").strip() or Path(filename).stem.strip() or "未命名资料"
        if len(name) > 128:
            raise BusinessRuleError(status_code=422, detail="资料名称不能超过128个字符")
        return name

    @staticmethod
    def _visible_category(category: str) -> str:
        return category if category in APPLICATION_DOCUMENT_CATEGORIES else "other"

    @staticmethod
    def _validate_pdf(filename: str, content_type: str | None, data: bytes) -> None:
        if not filename.lower().endswith(".pdf") or not data.startswith(b"%PDF-"):
            raise BusinessRuleError(status_code=400, detail="只支持有效的PDF文件")
        if content_type and content_type not in {"application/pdf", "application/octet-stream"}:
            raise BusinessRuleError(status_code=400, detail="文件类型不是PDF")
        if len(data) > settings.document_max_file_size:
            raise BusinessRuleError(status_code=413, detail="PDF文件超过大小限制")

    def _next_order(self, model, owner_column: str, owner_id: str) -> int:
        maximum = self.db.scalar(select(func.max(model.sort_order)).where(getattr(model, owner_column) == owner_id))
        return int(maximum or 0) + 1
