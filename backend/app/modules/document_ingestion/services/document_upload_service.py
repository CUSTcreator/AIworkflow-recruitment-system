from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from pathlib import Path

from backend.app.shared.audit import record_audit_event
from backend.app.shared.errors import BusinessRuleError
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.infrastructure.workflow_runtime import WorkflowQueue
from backend.app.models.entities import (
    Candidate,
    JobDocumentImport,
    JobDraft,
    ResumeSubmission,
    SourceDocument,
    User,
    WorkflowRun,
    WorkflowArtifact,
)
from backend.app.storage.object_store import ObjectStore
from backend.app.modules.auth.public import assert_business_action
from backend.app.modules.candidates.public import CandidateIntakeProcessService
from backend.app.modules.document_ingestion.services.job_document_import_process_service import (
    JobDocumentImportProcessService,
    JobDocumentImportStatus,
)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12].upper()}"


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class DocumentUploadService:
    def __init__(self, db: Session, store: ObjectStore | None = None) -> None:
        self.db = db
        self.store = store or ObjectStore()
        self.queue = WorkflowQueue(db)

    def upload_job_document(
        self,
        *,
        user: User,
        filename: str,
        content_type: str | None,
        data: bytes,
    ) -> tuple[SourceDocument, JobDocumentImport, WorkflowRun, bool]:

        # 1.权限校验
        assert_business_action(
            self.db,
            user,
            "job_document.upload",
            require_organization_scope=True,
        )
        filename = Path(filename).name.strip() or "job-requirements.xlsx"
        self._validate_filename(filename)
        document_format, stored_content_type = self._validate_job_document(
            filename, content_type, data
        )
        # 2. 相同文件只在仍有待处理草稿或仍在运行时复用当前导入；终态导入允许重新发起。
        source_sha256 = hashlib.sha256(data).hexdigest()
        existing_imports = list(self.db.execute(
            select(JobDocumentImport, SourceDocument)
            .join(
                SourceDocument,
                SourceDocument.source_document_id == JobDocumentImport.source_document_id,
            )
            .where(
                SourceDocument.document_type == "job_requirement",
                SourceDocument.source_sha256 == source_sha256,
            )
            .order_by(JobDocumentImport.updated_at.desc())
        ))
        for import_task, existing in existing_imports:
            has_pending_draft = self.db.scalar(
                select(JobDraft.job_draft_id)
                .where(
                    JobDraft.source_document_id == existing.source_document_id,
                    JobDraft.status == "draft",
                )
                .limit(1)
            ) is not None
            if import_task.status not in {
                JobDocumentImportStatus.QUEUED.value,
                JobDocumentImportStatus.PROCESSING.value,
            } and not has_pending_draft:
                continue
            run = self.queue.active_run(
                workflow_type="job_document_import_workflow",
                subject_type="job_document_import",
                subject_id=import_task.job_document_import_id,
            )
            if run is None:
                run = self.db.scalars(
                    select(WorkflowRun)
                    .where(
                        WorkflowRun.workflow_type == "job_document_import_workflow",
                        WorkflowRun.subject_type == "job_document_import",
                        WorkflowRun.subject_id == import_task.job_document_import_id,
                    )
                    .order_by(WorkflowRun.updated_at.desc())
                ).first()
            if run is None:
                raise RuntimeError("reusable_job_document_run_not_found")
            return existing, import_task, run, True
        #3.写入minIO
        document_id = _id("DOC")
        object_ref, stored_sha256 = self.store.put_bytes(
            f"documents/job_requirement/{source_sha256}.{document_format}",
            data,
            stored_content_type,
        )
        if stored_sha256 != source_sha256:
            raise RuntimeError("stored_document_hash_mismatch")
        #4.创建 SourceDocument数据对象
        document = SourceDocument(
            source_document_id=document_id,
            document_type="job_requirement",
            original_filename=filename,
            content_type=stored_content_type,
            object_ref=object_ref,
            source_sha256=source_sha256,
            parsed_text=None,
            # 岗位导入不在 SourceDocument 写业务元数据；文件格式可由名称和 MIME 推导。
            parser_provider=None,
            document_blocks_ref=None,
            document_blocks_sha256=None,
            document_blocks_schema_version=None,
            external_document_id=None,
            uploaded_by=user.user_id,
            created_at=_now(),
            updated_at=_now(),
        )
        self.db.add(document)
        self.db.flush()
        import_task = JobDocumentImport(
            job_document_import_id=_id("JIMP"),
            source_document_id=document.source_document_id,
            status=JobDocumentImportStatus.QUEUED.value,
            uploaded_by=user.user_id,
            created_at=_now(),
            updated_at=_now(),
        )
        self.db.add(import_task)
        self.db.flush()
        run = self._enqueue(import_task, document, user.user_id)
        JobDocumentImportProcessService.queue(
            import_task, workflow_run_id=run.workflow_run_id
        )
        # 文件对象、后台任务与审计行必须一并提交；不保存文件内容或哈希到审计 details。
        record_audit_event(
            self.db, actor=user, action="job_document.uploaded",
            target_type="source_document", target_id=document.source_document_id,
            summary=f"上传岗位要求文件：{document.original_filename}",
            details={"workflowRunId": run.workflow_run_id, "documentType": document.document_type},
            workflow_run_id=run.workflow_run_id,
        )
        return document, import_task, run, False


    def upload_resume_document(
        self,
        *,
        user: User,
        filename: str,
        content_type: str | None,
        data: bytes,
        candidate_id: str | None = None,
        rebuild_from_submission_id: str | None = None,
        authorization_permission: str = "resume.upload",
    ) -> tuple[SourceDocument, ResumeSubmission, WorkflowRun, bool]:
        '''
            简历上传是候选人生命周期的入口：PDF 入库后立即创建 Candidate，
            页面先显示稳定的候选人条目，解析、结构化和岗位匹配结果再逐步回填。
        '''
        filename = Path(filename).name.strip() or "resume.pdf"
        self._validate_filename(filename)
        self._validate_pdf(filename, content_type, data)
        # 首次上传和替换已有简历共用文件入库流程，但不是同一个业务职责。
        # 调用方必须显式传入已由命令边界校验的原子权限，避免替换操作又被
        # ``resume.upload`` 隐式收紧，或绕过对新权限的二次防御校验。
        assert_business_action(self.db, user, authorization_permission)
        if candidate_id is not None:
            target_candidate = self.db.get(Candidate, candidate_id)
            if target_candidate is None:
                raise BusinessRuleError(status_code=404, detail="候选人不存在")
            if target_candidate.status == "archived":
                raise BusinessRuleError(status_code=409, detail="候选人档案已删除，不能继续上传新版简历")
        if rebuild_from_submission_id is not None:
            previous = self.db.get(ResumeSubmission, rebuild_from_submission_id)
            if previous is None or previous.candidate_id != candidate_id:
                raise BusinessRuleError(status_code=409, detail="新版简历必须关联同一候选人的当前简历")
        # 1.查找是否已经上传过相同内容的简历
        source_sha256 = hashlib.sha256(data).hexdigest()
        # 相同文件仅与未删除 Candidate 的历史简历冲突。archived Candidate
        # 只是软删除审计记录，不应阻止用户再次上传同一份 PDF。
        existing = self.db.scalar(
            select(SourceDocument)
            .join(ResumeSubmission, ResumeSubmission.source_document_id == SourceDocument.source_document_id)
            .outerjoin(Candidate, Candidate.candidate_id == ResumeSubmission.candidate_id)
            .where(
                SourceDocument.document_type == "resume",
                SourceDocument.source_sha256 == source_sha256,
                # candidate_id 为空的历史数据无法判定为已删除，继续保护其去重语义。
                or_(ResumeSubmission.candidate_id.is_(None), Candidate.status != "archived"),
            )
            .order_by(SourceDocument.updated_at.desc())
        )
        if existing is not None:
            raise BusinessRuleError(
                status_code=409,
                code="duplicate_document",
                detail="该简历文件已上传过",
                context={"documentId": existing.source_document_id, "documentType": "resume"},
            )
        document_id = _id("DOC")
        object_ref, stored_sha256 = self.store.put_bytes(
            f"documents/resume/{source_sha256}.pdf", data, "application/pdf"
        )
        if stored_sha256 != source_sha256:
            raise RuntimeError("stored_document_hash_mismatch")
        # 2.创建简历类型的 SourceDocument
        document = SourceDocument(
            source_document_id=document_id,
            document_type="resume",
            original_filename=filename,
            content_type="application/pdf",
            object_ref=object_ref,
            source_sha256=source_sha256,
            # 原始文件已落对象存储并且本事务会创建 WorkflowRun，因此直接进入排队态。
            parsed_text=None,
            external_document_id=None,
            uploaded_by=user.user_id,
            created_at=_now(),
            updated_at=_now(),
        )
        self.db.add(document)
        self.db.flush()

        # 3.若未传入 candidate_id，说明是首次上传
        if candidate_id is None:
            candidate = Candidate(
                candidate_id=_id("CAND"),
                display_name="待解析候选人",
                status="active",
                created_at=_now(),
                updated_at=_now(),
            )
            self.db.add(candidate)
            self.db.flush()
            candidate_id = candidate.candidate_id
        # 4.创建 ResumeSubmission
        submission = ResumeSubmission(
            resume_submission_id=_id("RSUB"),
            source_document_id=document.source_document_id,
            job_id=None,
            idempotency_key=f"upload:{uuid.uuid4().hex}",
            external_candidate_id=None,
            external_application_id=None,
            candidate_name_override=None,
            status="queued",
            candidate_id=candidate_id,
            intake_mode="replacement" if rebuild_from_submission_id else "initial",
            supersedes_submission_id=rebuild_from_submission_id,
            rebuild_from_submission_id=rebuild_from_submission_id,
            application_id=None,
            business_submitted_at=_now(),
            error_message=None,
            uploaded_by=user.user_id,
            created_at=_now(),
            updated_at=_now(),
        )
        self.db.add(submission)
        self.db.flush()
        candidate = self.db.get(Candidate, candidate_id) if candidate_id else None
        if candidate is not None:
            now = _now()
            candidate.current_resume_submission_id = submission.resume_submission_id
            CandidateIntakeProcessService.activate_candidate(candidate, now=now)
        run = self._enqueue_resume_submission(submission, user.user_id)
        # 初次上传与替换上传都在同一入口审计；仅记录文件名、对象 ID 和模式。
        record_audit_event(
            self.db, actor=user, action="candidate.resume.uploaded",
            target_type="resume_submission", target_id=submission.resume_submission_id,
            summary=("上传候选人新版简历" if rebuild_from_submission_id else "上传候选人简历"),
            details={
                "candidateId": candidate_id,
                "sourceDocumentId": document.source_document_id,
                "filename": document.original_filename,
                "mode": submission.intake_mode,
            },
            workflow_run_id=run.workflow_run_id,
        )
        return document, submission, run, False

    def retry(self, document: SourceDocument, user: User) -> WorkflowRun:
        assert_business_action(
            self.db,
            user,
            "job_document.upload",
            require_organization_scope=True,
        )
        import_task = JobDocumentImportProcessService.by_source_document(
            self.db, document.source_document_id, for_update=True
        )
        if import_task is None:
            raise BusinessRuleError(status_code=409, detail="岗位导入任务不存在，请重新上传文件")
        if import_task.status not in {"failed", "review_required"}:
            raise BusinessRuleError(status_code=409, detail="只有失败或待确认的文档可以重试")
        if import_task.status == "review_required":
            pending_draft = self.db.scalar(
                select(JobDraft.job_draft_id)
                .where(
                    JobDraft.source_document_id == document.source_document_id,
                    JobDraft.status == "draft",
                )
                .limit(1)
            )
            if pending_draft is not None:
                raise BusinessRuleError(
                    status_code=409,
                    detail="当前文档已有未确认岗位草稿，请先完成确认或删除草稿后再重新处理",
                )
        active = self.queue.active_run(
            workflow_type="job_document_import_workflow",
            subject_type="job_document_import",
            subject_id=import_task.job_document_import_id,
        )
        if active is not None:
            return active
        retry_input: dict[str, object] = {}
        if import_task.recovery_code == "job_draft_publish_retryable":
            artifact = self.db.scalar(
                select(WorkflowArtifact)
                .join(WorkflowRun, WorkflowRun.workflow_run_id == WorkflowArtifact.workflow_run_id)
                .where(
                    WorkflowRun.subject_type == "job_document_import",
                    WorkflowRun.subject_id == import_task.job_document_import_id,
                    WorkflowArtifact.artifact_type == "job_document_extraction",
                )
                .order_by(WorkflowArtifact.created_at.desc())
                .limit(1)
            )
            if artifact is None:
                raise BusinessRuleError(status_code=409, code="job_extraction_artifact_missing", detail="岗位提取结果已不可用，请重新解析文件")
            retry_input = {"retryMode": "publish_drafts", "extractionArtifactId": artifact.artifact_id}
        run = self._enqueue(import_task, document, user.user_id, input_json=retry_input)
        JobDocumentImportProcessService.queue(
            import_task, workflow_run_id=run.workflow_run_id
        )
        record_audit_event(
            self.db, actor=user, action="job_document.retry_requested",
            target_type="source_document", target_id=document.source_document_id,
            summary="人工重新提交岗位要求文件处理任务",
            details={"workflowRunId": run.workflow_run_id}, workflow_run_id=run.workflow_run_id,
        )
        return run

    def submit_repair(
        self,
        document: SourceDocument,
        user: User,
        *,
        action: str,
        sheet_name: str | None = None,
        header_row_index: int | None = None,
        header_mapping: dict[str, int] | None = None,
        fields: dict[str, dict[str, object]] | None = None,
    ) -> WorkflowRun:
        """保存用户修复输入并从岗位提取 Step 重新运行。

        人工输入只进入新 WorkflowRun 的 input_json；原始文件和旧工件保持不变，
        这样重试可审计、可幂等，也不会在 HTTP 请求中直接改写解析产物。
        """
        assert_business_action(self.db, user, "job_document.upload", require_organization_scope=True)
        import_task = JobDocumentImportProcessService.by_source_document(
            self.db, document.source_document_id, for_update=True
        )
        if import_task is None:
            raise BusinessRuleError(status_code=404, detail="岗位导入任务不存在，请重新上传文件")
        if import_task.status not in {"failed", "review_required"}:
            raise BusinessRuleError(status_code=409, detail="当前岗位导入状态不需要人工修复")
        if action not in {"review_job_headers", "review_job_fields"}:
            raise BusinessRuleError(status_code=400, detail="不支持的岗位修复动作")
        if action == "review_job_headers":
            allowed = {"sequence", "department", "title", "headcount", "responsibilities", "qualifications", "education", "major"}
            mapping = dict(header_mapping or {})
            if not sheet_name or header_row_index is None or "title" not in mapping:
                raise BusinessRuleError(status_code=409, code="job_header_mapping_incomplete", detail="请提供工作表、表头行和职位名称列")
            if any(key not in allowed or isinstance(value, bool) or not isinstance(value, int) or value < 0 for key, value in mapping.items()):
                raise BusinessRuleError(status_code=409, code="job_header_mapping_invalid", detail="表头列映射无效，请重新选择")
            if len(set(mapping.values())) != len(mapping):
                raise BusinessRuleError(status_code=409, code="job_header_mapping_duplicate", detail="多个字段不能映射到同一列")
            repair_context: dict[str, object] = {
                "sheetName": sheet_name,
                "headerRowIndex": header_row_index,
                "headerMapping": mapping,
            }
        else:
            normalized_fields = dict(fields or {})
            if not normalized_fields:
                raise BusinessRuleError(status_code=409, code="job_field_repair_empty", detail="请至少校正一个岗位字段")
            if len(normalized_fields) > 500:
                raise BusinessRuleError(status_code=400, detail="一次提交的岗位字段过多")
            repair_context = {"fields": normalized_fields}
        active = self.queue.active_run(
            workflow_type="job_document_import_workflow",
            subject_type="job_document_import",
            subject_id=import_task.job_document_import_id,
        )
        if active is not None:
            return active
        run = self._enqueue(
            import_task,
            document,
            user.user_id,
            input_json={"retryMode": "manual_repair", "repairAction": action, "repairContext": repair_context},
        )
        JobDocumentImportProcessService.queue(import_task, workflow_run_id=run.workflow_run_id)
        record_audit_event(
            self.db,
            actor=user,
            action="job_document.repair_requested",
            target_type="source_document",
            target_id=document.source_document_id,
            summary="提交岗位导入人工修复",
            details={"workflowRunId": run.workflow_run_id, "repairAction": action},
            workflow_run_id=run.workflow_run_id,
        )
        return run

    def _latest_or_enqueue(self, document: SourceDocument, user_id: str) -> WorkflowRun:
        import_task = JobDocumentImportProcessService.by_source_document(
            self.db, document.source_document_id
        )
        if import_task is None:
            raise RuntimeError("job_document_import_missing")
        run = self.db.scalar(
            select(WorkflowRun)
            .where(
                WorkflowRun.subject_type == "job_document_import",
                WorkflowRun.subject_id == import_task.job_document_import_id,
                WorkflowRun.workflow_type == "job_document_import_workflow",
            )
            .order_by(WorkflowRun.started_at.desc())
        )
        return run or self._enqueue(import_task, document, user_id)

    def _enqueue(
        self,
        import_task: JobDocumentImport,
        document: SourceDocument,
        user_id: str,
        *,
        input_json: dict[str, object] | None = None,
    ) -> WorkflowRun:
        run, _ = self.queue.enqueue(
            workflow_type="job_document_import_workflow",
            subject_type="job_document_import",
            subject_id=import_task.job_document_import_id,
            triggered_by=user_id,
            input_json={
                "schemaVersion": "job_document_import_input_v2",
                "jobDocumentImportId": import_task.job_document_import_id,
                "sourceDocumentId": document.source_document_id,
                "documentType": document.document_type,
                "sourceSha256": document.source_sha256,
                **dict(input_json or {}),
            },
            # 岗位导入 v2 在草稿发布前增加确认前要求分类 Step。
            definition_version=2,
            reuse_active=False,
        )
        # JobDocumentImport.workflow_run_id has a database foreign key.  Flush the
        # new run before callers associate it with the import task; SQLAlchemy has
        # no ORM relationship between these string IDs to infer the insert order.
        self.db.flush([run])
        return run

    def _latest_submission_run(
        self, submission: ResumeSubmission
    ) -> WorkflowRun | None:
        return self.db.scalar(
            select(WorkflowRun)
            .where(
                WorkflowRun.subject_type == "resume_submission",
                WorkflowRun.subject_id == submission.resume_submission_id,
                WorkflowRun.workflow_type == "resume_document_import_workflow",
            )
            .order_by(WorkflowRun.started_at.desc())
        )

    def _enqueue_resume_submission(
        self, submission: ResumeSubmission, user_id: str
    ) -> WorkflowRun:
        document = self.db.get(SourceDocument, submission.source_document_id)
        if document is None:
            raise RuntimeError("resume_submission_source_document_missing")
        run, _ = self.queue.enqueue(
            workflow_type="resume_document_import_workflow",
            subject_type="resume_submission",
            subject_id=submission.resume_submission_id,
            triggered_by=user_id,
            input_json={
                "schemaVersion": "resume_document_import_input_v1",
                "resumeSubmissionId": submission.resume_submission_id,
                "candidateId": submission.candidate_id,
                "sourceDocumentId": submission.source_document_id,
                "sourceSha256": document.source_sha256,
            },
            reuse_active=False,
        )
        return run

    @staticmethod
    def _validate_filename(filename: str) -> None:
        if not filename or len(filename) > 128:
            raise BusinessRuleError(status_code=400, detail="文件名不能为空且不能超过128个字符")

    @staticmethod
    def _validate_pdf(filename: str, content_type: str | None, data: bytes) -> None:
        if not filename.lower().endswith(".pdf"):
            raise BusinessRuleError(status_code=400, detail="只支持PDF文件")
        if content_type and content_type not in {
            "application/pdf",
            "application/octet-stream",
        }:
            raise BusinessRuleError(status_code=400, detail="文件类型不是PDF")
        if not data.startswith(b"%PDF-"):
            raise BusinessRuleError(status_code=400, detail="文件内容不是有效PDF")
        if len(data) > settings.document_max_file_size:
            raise BusinessRuleError(status_code=413, detail="PDF文件超过大小限制")

    @staticmethod
    def _validate_job_document(
        filename: str,
        content_type: str | None,
        data: bytes,
    ) -> tuple[str, str]:
        lowered = filename.lower()
        if not lowered.endswith(".xlsx"):
            raise BusinessRuleError(
                status_code=400,
                detail="招聘要求只支持XLSX文件",
            )
        if content_type and content_type not in {
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/octet-stream",
            "application/zip",
        }:
            raise BusinessRuleError(status_code=400, detail="文件类型不是XLSX")
        if not data.startswith(b"PK"):
            raise BusinessRuleError(status_code=400, detail="文件内容不是有效XLSX")
        if len(data) > settings.document_max_file_size:
            raise BusinessRuleError(status_code=413, detail="XLSX文件超过大小限制")
        return (
            "xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
