from __future__ import annotations

import uuid
import re
from datetime import UTC, datetime

from backend.app.shared.errors import BusinessRuleError
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.job_identity import (
    jd_content_sha256,
    job_position_identity_key,
    job_requisition_identity_key,
    normalize_jd_text,
    normalize_job_title,
)
from backend.app.models.entities import (
    Department,
    Job,
    JobDocumentImport,
    JobDraft,
    JobPosition,
    JobVersionRecord,
    SourceDocument,
    User,
)
from backend.app.modules.auth.public import assert_business_action, has_permission
from backend.app.shared.audit import record_audit_event
from backend.app.modules.jobs.public import JobProfileService
from backend.app.modules.jobs.public import JobProcessService

from ..readModel.job_draft_read_model import job_draft_view, mark_user_override, unresolved_fields
from .document_upload_service import DocumentUploadService
from .job_document_import_process_service import JobDocumentImportProcessService


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12].upper()}"


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class JobDocumentService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.uploads = DocumentUploadService(db)

    def get_document(self, document_id: str, user: User) -> SourceDocument:
        assert_business_action(
            self.db,
            user,
            "job_document.upload",
            require_organization_scope=True,
        )
        document = self.db.get(SourceDocument, document_id)
        if document is None or document.document_type != "job_requirement":
            raise BusinessRuleError(status_code=404, detail="未找到招聘要求文档")
        return document

    def get_import(self, document_id: str, user: User) -> JobDocumentImport:
        self.get_document(document_id, user)
        import_task = JobDocumentImportProcessService.by_source_document(
            self.db, document_id
        )
        if import_task is None:
            raise BusinessRuleError(status_code=404, detail="未找到岗位导入任务")
        return import_task

    def list_drafts(self, document_id: str, user: User) -> list:
        """返回当前可处理的草稿；逻辑删除项只保留在审计数据中。"""
        self.get_document(document_id, user)
        # GET 只计算当前展示所需的重复状态，不能提交数据库事务。
        self._refresh_duplicate_state(document_id)
        drafts = list(
            self.db.scalars(
                select(JobDraft)
                .where(
                    JobDraft.source_document_id == document_id,
                    JobDraft.status != "deleted",
                )
                .order_by(JobDraft.sequence_no)
            )
        )
        return [job_draft_view(draft) for draft in drafts]

    def update_draft(
        self,
        document_id: str,
        draft_id: str,
        user: User,
        changes: dict,
    ) -> object:
        submitted_hard_screening_rules = changes.pop("hard_screening_rules", None)
        document = self.get_document(document_id, user)
        import_task = self.get_import(document_id, user)
        if import_task.status not in {"review_required", "partially_confirmed"}:
            raise BusinessRuleError(status_code=409, detail="当前文档状态不允许修改岗位草稿")
        draft = self.db.get(JobDraft, draft_id)
        if draft is None or draft.source_document_id != document_id:
            raise BusinessRuleError(status_code=404, detail="未找到岗位草稿")
        if draft.status != "draft":
            raise BusinessRuleError(status_code=409, detail="只有未确认岗位草稿可以继续修改")
        department_changed = (
            "department_id" in changes
            and draft.department_id != changes.get("department_id")
        )
        if department_changed:
            department_id = changes["department_id"]
            if department_id:
                department = self.db.get(Department, department_id)
                if department is None or department.deleted_at is not None:
                    raise BusinessRuleError(status_code=400, detail="所选部门不存在")
                draft.department_match_status = "manually_resolved"
            else:
                draft.department_match_status = "auto_create" if draft.source_department_name else "unmatched"
        editable_fields = (
            "title",
            "headcount",
            "responsibilities",
            "qualifications",
            "education_requirement",
            "major_requirement",
            "department_id",
        )
        changed_fields = {
            field for field in editable_fields
            if field in changes and getattr(draft, field) != changes[field]
        }
        for field in changed_fields:
            setattr(draft, field, changes[field])

        if {"education_requirement", "major_requirement", "qualifications"} & changed_fields:
            # 预分类的 source_quote 必须与用户保存后的任职要求一致。HTTP PATCH 中不调用
            # LLM；源字段被修改时只做确定性重算并标记降级，保证画像不会继续消费旧引用。
            from recruitment_ai_core.job_capability.requirement_classification import (
                deterministic_requirement_fallback,
            )

            draft.requirement_classification_json = deterministic_requirement_fallback({
                "education_requirement": draft.education_requirement,
                "major_requirement": draft.major_requirement,
                "major_requirement_source_quote": draft.major_requirement_source_quote,
                "qualifications": list(draft.qualifications or []),
            })
        if submitted_hard_screening_rules is not None:
            classification = dict(draft.requirement_classification_json or {})
            classification["hardScreeningRules"] = [
                dict(item) for item in submitted_hard_screening_rules
            ]
            draft.requirement_classification_json = classification
        if "resolution" in changes:
            draft.resolution = changes["resolution"]
        mark_user_override(draft, changed_fields)
        if "preset_model_id" in changes and changes["preset_model_id"]:
            draft.preset_model_id = str(changes["preset_model_id"])
            draft.preset_model_version = "1.0"
            draft.preset_model_selection_source = "recruiter_confirmed"
        draft.updated_at = _now()
        self._refresh_duplicate_state(document_id)
        # 仅审计被修改的字段名，不复制职责、任职资格等大段业务文本。
        record_audit_event(
            self.db, actor=user, action="job_document.draft.updated",
            target_type="job_draft", target_id=draft.job_draft_id,
            summary=f"修改岗位草稿：{draft.title}",
            details={"sourceDocumentId": document.source_document_id, "changedFields": sorted(changed_fields)},
        )
        self.db.flush()
        return job_draft_view(draft)

    def delete_draft(self, document_id: str, draft_id: str, user: User) -> dict[str, str]:
        """逻辑删除一条未确认草稿，不影响源文件、工作流或任何正式岗位。"""
        assert_business_action(
            self.db, user, "job_document.confirm", require_organization_scope=True,
        )
        document = self.get_document(document_id, user)
        import_task = self.get_import(document_id, user)
        if import_task.status not in {"review_required", "partially_confirmed"}:
            raise BusinessRuleError(status_code=409, detail="当前文档状态不允许删除岗位草稿")
        draft = self.db.get(JobDraft, draft_id)
        if draft is None or draft.source_document_id != document_id:
            raise BusinessRuleError(status_code=404, detail="未找到岗位草稿")
        if draft.status != "draft":
            raise BusinessRuleError(status_code=409, detail="只有未确认岗位草稿可以删除")
        JobProcessService.delete_draft(draft, now=_now())
        self._refresh_duplicate_state(document_id)
        self._sync_import_review_status(import_task, document)
        record_audit_event(
            self.db, actor=user, action="job_document.draft.deleted",
            target_type="job_draft", target_id=draft.job_draft_id,
            summary=f"删除未确认岗位草稿：{draft.title}",
            details={"sourceDocumentId": document.source_document_id},
        )
        self.db.flush()
        return {
            "document_id": document.source_document_id,
            "draft_id": draft.job_draft_id,
            "draft_status": draft.status,
            "import_status": import_task.status,
        }

    def _sync_import_review_status(
        self, import_task: JobDocumentImport, document: SourceDocument
    ) -> None:
        """由剩余 draft 决定导入是否仍待处理；confirmed/skipped/deleted 均为终态。"""
        drafts = list(self.db.scalars(
            select(JobDraft).where(JobDraft.source_document_id == document.source_document_id)
        ))
        JobDocumentImportProcessService.sync_drafts(import_task, drafts)
    def _refresh_duplicate_state(self, document_id: str) -> None:
        drafts = list(self.db.scalars(select(JobDraft).where(
            JobDraft.source_document_id == document_id,
            JobDraft.status == "draft",
        ).order_by(JobDraft.sequence_no)))
        groups: dict[tuple[str, str], list[JobDraft]] = {}
        for draft in drafts:
            draft.duplicate_status = "new_job"
            draft.existing_job_id = None
            draft.duplicate_group_id = None
            if not draft.department_id or not normalize_job_title(draft.title):
                continue
            key = (draft.department_id, normalize_job_title(draft.title))
            groups.setdefault(key, []).append(draft)
        for (department_id, normalized_title), members in groups.items():
            position = self.db.scalar(select(JobPosition).where(
                JobPosition.department_id == department_id,
                JobPosition.normalized_title == normalized_title,
            ))
            existing = None
            if position is not None:
                existing = self.db.scalar(select(Job).where(
                    Job.position_id == position.position_id,
                    Job.deleted_at.is_(None),
                ).order_by(Job.opened_at.desc(), Job.job_id.desc()))
            if len(members) > 1:
                group_id = f"{department_id}:{normalized_title}"
                for item in members:
                    item.duplicate_status = "same_upload"
                    item.duplicate_group_id = group_id
                    item.existing_job_id = existing.job_id if existing else None
                    if item.resolution not in {"keep", "overwrite", "skip"}:
                        item.resolution = None
                continue
            draft = members[0]
            if existing is not None:
                draft.duplicate_status = "existing_job"
                draft.existing_job_id = existing.job_id
                if draft.resolution not in {"overwrite", "skip"}:
                    draft.resolution = None
            else:
                draft.duplicate_status = "new_job"
                draft.resolution = "create"

    def _validate_duplicate_resolutions(self, drafts: list[JobDraft]) -> None:
        unresolved: list[str] = []
        groups: dict[str, list[JobDraft]] = {}
        for draft in drafts:
            if draft.status != "draft":
                continue
            if draft.duplicate_status == "existing_job" and draft.resolution not in {"overwrite", "skip"}:
                unresolved.append(draft.title)
            elif draft.duplicate_status == "same_upload":
                groups.setdefault(str(draft.duplicate_group_id or draft.job_draft_id), []).append(draft)
        for members in groups.values():
            has_existing_job = any(item.existing_job_id for item in members)
            allowed_primary = {"overwrite"} if has_existing_job else {"keep"}
            primary = [item for item in members if item.resolution in allowed_primary]
            skipped = [item for item in members if item.resolution == "skip"]
            if len(primary) != 1 or len(skipped) != len(members) - 1:
                unresolved.extend(item.title for item in members)
        if unresolved:
            raise BusinessRuleError(
                status_code=409,
                code="duplicate_resolution_required",
                detail="存在未处理的重复岗位，请选择覆盖或跳过",
                context={"draftTitles": list(dict.fromkeys(unresolved))},
            )
    def confirm_drafts(
        self,
        document_id: str,
        user: User,
        draft_ids: list[str] | None,
        *,
        confirmed_hard_screening_rules: dict[str, list[dict]] | None = None,
    ) -> list[dict]:
        assert_business_action(
            self.db,
            user,
            "job_document.confirm",
            require_organization_scope=True,
        )
        document = self.get_document(document_id, user)
        import_task = self.get_import(document_id, user)
        if import_task.status not in {
            "review_required",
            "partially_confirmed",
            "completed",
        }:
            raise BusinessRuleError(status_code=409, detail="岗位草稿尚未准备完成")
        query = (
            select(JobDraft)
            .where(
                JobDraft.source_document_id == document_id,
                JobDraft.status != "deleted",
            )
            .order_by(JobDraft.sequence_no)
        )
        all_drafts = list(self.db.scalars(query))
        drafts = list(all_drafts)
        if draft_ids is not None:
            requested = set(draft_ids)
            drafts = [draft for draft in drafts if draft.job_draft_id in requested]
            if len(drafts) != len(requested):
                raise BusinessRuleError(status_code=404, detail="部分岗位草稿不存在")
        if not drafts:
            raise BusinessRuleError(status_code=400, detail="没有可确认的岗位草稿")
        for draft in drafts:
            if draft.status != "confirmed" and not draft.department_id:
                self._resolve_or_create_department(draft, user)
        unresolved = [
            draft.title
            for draft in drafts
            if draft.status != "confirmed" and not draft.department_id
        ]
        if unresolved:
            raise BusinessRuleError(
                status_code=409,
                detail="以下岗位的部门未匹配，请先确认部门：" + "、".join(unresolved),
            )
        extraction_unresolved = [
            f"{draft.title}（{','.join(unresolved_fields(draft))}）"
            for draft in drafts
            if draft.status != "confirmed" and unresolved_fields(draft)
        ]
        if extraction_unresolved:
            raise BusinessRuleError(
                status_code=409,
                detail="以下岗位仍有待确认字段：" + "、".join(extraction_unresolved),
            )
        self._refresh_duplicate_state(document_id)
        self._validate_duplicate_resolutions(all_drafts)

        results: list[dict] = []
        for draft in drafts:
            if draft.status == "confirmed" and draft.confirmed_job_id:
                results.append({"job_id": draft.confirmed_job_id, "job_draft_id": draft.job_draft_id, "title": draft.title, "reused": True, "skipped": False})
                continue
            if draft.resolution == "skip":
                JobProcessService.skip_draft(draft, now=_now())
                results.append({"job_id": None, "job_draft_id": draft.job_draft_id, "title": draft.title, "reused": False, "skipped": True})
                continue
            # 1. 确认草稿后只发布 Job 与不可变 JobVersion；不得在 HTTP 请求中调用 LLM。
            if draft.resolution == "overwrite":
                job = self.db.get(Job, draft.existing_job_id)
                if job is None or job.deleted_at is not None:
                    raise BusinessRuleError(status_code=409, code="duplicate_resolution_stale", detail="待覆盖岗位已变化，请重新确认")
                version = self._update_requisition(
                    job,
                    draft=draft,
                    jd_text=normalize_jd_text(self._draft_text(draft)),
                    source_sha256=jd_content_sha256(normalize_jd_text(self._draft_text(draft))),
                )
                reused = True
            else:
                job, reused, version = self._create_or_reuse_job(draft)

            submitted_rules = (
                confirmed_hard_screening_rules.get(draft.job_draft_id)
                if confirmed_hard_screening_rules is not None
                and draft.job_draft_id in confirmed_hard_screening_rules
                else self._default_hard_screening_rules(draft)
            )
            # 用户最终提交的规则与分类 items 一起冻结；画像阶段只复用这份已确认输入，
            # 不重复调用要求分类 LLM，也不会重新生成另一套硬筛建议。
            classification = dict(draft.requirement_classification_json or {})
            classification["hardScreeningRules"] = [dict(item) for item in submitted_rules]
            draft.requirement_classification_json = classification
            version.frozen_job_json = {
                **dict(version.frozen_job_json or {}),
                "requirement_classification": classification,
            }

            profile_service = JobProfileService(self.db)
            # 岗位及其硬筛条件是同一次用户确认。画像 Workflow 只能补充能力画像，
            # 不得在后台再生成一个要求用户二次确认的替代策略。
            profile_service.publish_confirmed_hard_screening_policy(
                version,
                actor_id=user.user_id,
                rules=submitted_rules,
            )

            # 2. 同一数据库事务内写入 WorkflowRun；提交后 Worker 才能看到冻结 JD 版本。
            profile_run = profile_service.enqueue_for_version(
                version,
                triggered_by=user.user_id,
            )
            JobProcessService.confirm_draft(draft, job_id=job.job_id, now=_now())
            results.append({
                "job_id": job.job_id,
                "job_draft_id": draft.job_draft_id,
                "title": draft.title,
                "reused": reused,
                "skipped": False,
                "jd_version_id": version.jd_version_id,
                "profile_status": version.profile_status,
                "profile_workflow_run_id": profile_run.workflow_run_id if profile_run else None,
            })
        self.db.flush()
        self._sync_import_review_status(import_task, document)
        return results

    @staticmethod
    def _default_hard_screening_rules(draft: JobDraft) -> list[dict]:
        """未显式提交时使用确认前分类 Step 已保存的预览结果。"""
        result = dict(draft.requirement_classification_json or {})
        if not result:
            # 仅为迁移前历史草稿保留保守规则回退，不在确认接口调用 LLM。
            from recruitment_ai_core.job_capability.requirement_classification import deterministic_requirement_fallback
            result = deterministic_requirement_fallback({
                "education_requirement": draft.education_requirement,
                "major_requirement": draft.major_requirement,
                "major_requirement_source_quote": draft.major_requirement_source_quote,
                "qualifications": list(draft.qualifications or []),
            })
        return [
            {**dict(item), "enabled": bool(item.get("enabled", True))}
            for item in list(result.get("hardScreeningRules") or [])
            if item.get("expected_value") not in (None, "")
        ]

    def _resolve_or_create_department(self, draft: JobDraft, actor: User) -> None:
        """Create only an explicit XLSX department name at recruiter confirmation time."""
        source_name = str(draft.source_department_name or "").strip()
        if not source_name:
            return
        normalized = self._normalize_department(source_name)
        if not normalized:
            return
        candidates = list(self.db.scalars(select(Department)))
        same_name = [item for item in candidates if self._normalize_department(item.name) == normalized]
        active = [item for item in same_name if item.deleted_at is None]
        if len(active) == 1:
            draft.department_id = active[0].department_id
            draft.department_match_status = "matched"
            return
        if len(active) > 1:
            draft.department_match_status = "ambiguous"
            return
        if same_name:
            draft.department_match_status = "deleted"
            return
        department = Department(
            department_id=_id("DEPT"),
            name=source_name[:128],
            is_active=True,
        )
        try:
            with self.db.begin_nested():
                self.db.add(department)
                self.db.flush()
        except IntegrityError:
            department = self.db.scalar(
                select(Department).where(func.lower(Department.name) == source_name.lower())
            )
            if department is None or department.deleted_at is not None:
                draft.department_match_status = "ambiguous" if department is None else "inactive"
                return
        draft.department_id = department.department_id
        draft.department_match_status = "auto_created"
        draft.department_auto_created = True
        record_audit_event(
            self.db,
            actor=actor,
            action="job_document.department.auto_create",
            target_type="department",
            target_id=department.department_id,
            summary=f"根据招聘要求创建部门：{department.name}",
            details={"sourceDocumentId": draft.source_document_id, "jobDraftId": draft.job_draft_id},
        )

    @staticmethod
    def _normalize_department(value: str) -> str:
        return re.sub(r"[\s:：()（）/／_-]+", "", value).casefold()

    def _create_or_reuse_job(self, draft: JobDraft) -> tuple[Job, bool, JobVersionRecord]:
        if not draft.department_id:
            raise BusinessRuleError(status_code=409, detail=f"{draft.title}尚未确认所属部门")
        jd_text = normalize_jd_text(self._draft_text(draft))
        source_sha256 = jd_content_sha256(jd_text)
        position = self._get_or_create_position(
            department_id=draft.department_id,
            title=draft.title,
        )
        # 已确认且仍可分发的岗位（open / setup_pending）都属于同一正式职位。
        # setup_pending 不能按旧 closed 语义遗漏，否则会绕过重复检查并创建多个
        # 同部门同名的待配置岗位。
        existing = self.db.scalar(
            select(Job)
            .where(
                Job.position_id == position.position_id,
                Job.status.in_(("open", "setup_pending")),
                Job.deleted_at.is_(None),
            )
            .order_by(Job.opened_at.desc(), Job.job_id.desc())
        )
        if existing is not None:
            raise BusinessRuleError(
                status_code=409,
                code="duplicate_resolution_required",
                detail="检测到同部门同名岗位，请明确选择覆盖或跳过",
            )

        manager_id = self._default_manager_id(draft.department_id)
        recruiter_id = self._default_recruiter_id(draft.department_id)
        # JD 已确认即可参与候选人分发；缺少岗位人员配置只进入 setup_pending，
        # 不能伪装成业务关闭。
        job_status = "open" if manager_id and recruiter_id else "setup_pending"
        job_id = _id("JOB")
        job = Job(
            job_id=job_id,
            position_id=position.position_id,
            external_job_id=None,
            identity_key=job_requisition_identity_key(
                position_id=position.position_id,
                requisition_id=job_id,
            ),
            jd_content_sha256=source_sha256,
            title=position.title,
            department_id=position.department_id,
            status=job_status,
            headcount=draft.headcount,
            source_document_id=draft.source_document_id,
            responsibilities=list(draft.responsibilities or []),
            qualifications=list(draft.qualifications or []),
            education_requirement=draft.education_requirement,
            major_requirement=draft.major_requirement,
            hiring_manager_id=manager_id,
            department_recruiter_id=recruiter_id,
            jd_text=jd_text,
            preset_model_id=draft.preset_model_id,
            preset_model_version=draft.preset_model_version,
            opened_at=_now(),
            # setup_pending 不是关闭岗位；只是不允许开始后续招聘流程，关闭时间必须为空。
            closed_at=None,
        )
        try:
            with self.db.begin_nested():
                self.db.add(job)
                self.db.flush()
        except IntegrityError:
            job = self.db.scalar(
                select(Job)
                .where(
                    Job.position_id == position.position_id,
                    Job.status.in_(("open", "setup_pending")),
                    Job.deleted_at.is_(None),
                )
                .order_by(Job.opened_at.desc(), Job.job_id.desc())
            )
            if job is None:
                raise
            version = self._update_requisition(
                job,
                draft=draft,
                jd_text=jd_text,
                source_sha256=source_sha256,
            )
            return job, True, version
        # JobVersion 是画像 workflow 的冻结输入；状态从 queued 开始，等待异步编译。
        version = JobVersionRecord(
            jd_version_id=_id("JDV"),
            job_id=job.job_id,
            version=1,
            source_sha256=source_sha256,
            source_text=jd_text,
            frozen_job_json=self._frozen_job_json(job),
            preset_model_id=job.preset_model_id,
            preset_model_version=job.preset_model_version,
            profile_status="queued",
            created_at=_now(),
        )
        self.db.add(version)
        self.db.flush()
        return job, False, version

    def _get_or_create_position(
        self,
        *,
        department_id: str,
        title: str,
    ) -> JobPosition:
        normalized_title = normalize_job_title(title)
        position = self.db.scalar(
            select(JobPosition).where(
                JobPosition.department_id == department_id,
                JobPosition.normalized_title == normalized_title,
            )
        )
        if position is None:
            identity = job_position_identity_key(
                department_id=department_id,
                title=title,
            )
            candidate = JobPosition(
                position_id=f"POS_{identity[:12].upper()}",
                department_id=department_id,
                title=title.strip(),
                normalized_title=normalized_title,
                created_at=_now(),
                updated_at=_now(),
            )
            try:
                with self.db.begin_nested():
                    self.db.add(candidate)
                    self.db.flush()
                position = candidate
            except IntegrityError:
                position = self.db.scalar(
                    select(JobPosition).where(
                        JobPosition.department_id == department_id,
                        JobPosition.normalized_title == normalized_title,
                    )
                )
                if position is None:
                    raise
        if position.title != title.strip():
            position.title = title.strip()
            position.updated_at = _now()
        return position

    def _update_requisition(
        self,
        job: Job,
        *,
        draft: JobDraft,
        jd_text: str,
        source_sha256: str,
    ) -> JobVersionRecord:
        setup_was_incomplete = not job.hiring_manager_id or not job.department_recruiter_id
        if not job.hiring_manager_id:
            job.hiring_manager_id = self._default_manager_id(draft.department_id)
        if not job.department_recruiter_id:
            job.department_recruiter_id = self._default_recruiter_id(draft.department_id)
        if setup_was_incomplete and job.hiring_manager_id and job.department_recruiter_id:
            # 待配置岗位补齐人员后发布；真正关闭的岗位按 reopen 恢复。
            # 两条路径都必须经过 Job 主状态机，不能直接覆盖 status。
            if job.status == "setup_pending":
                JobProcessService.publish_job(job, now=_now())
            elif job.status == "closed":
                JobProcessService.reopen_job(job, now=_now())
        job.headcount = draft.headcount
        job.source_document_id = draft.source_document_id
        job.responsibilities = list(draft.responsibilities or [])
        job.qualifications = list(draft.qualifications or [])
        job.education_requirement = draft.education_requirement
        job.major_requirement = draft.major_requirement
        job.jd_content_sha256 = source_sha256
        job.jd_text = jd_text
        job.title = draft.title
        job.department_id = draft.department_id
        job.preset_model_id = draft.preset_model_id
        job.preset_model_version = draft.preset_model_version
        version_exists = self.db.scalar(
            select(JobVersionRecord).where(
                JobVersionRecord.job_id == job.job_id,
                JobVersionRecord.source_sha256 == source_sha256,
            )
        )
        if version_exists is None:
            latest_version = self.db.scalar(
                select(func.max(JobVersionRecord.version)).where(
                    JobVersionRecord.job_id == job.job_id
                )
            ) or 0
            version = JobVersionRecord(
                jd_version_id=_id("JDV"),
                job_id=job.job_id,
                version=latest_version + 1,
                source_sha256=source_sha256,
                source_text=jd_text,
                frozen_job_json=self._frozen_job_json(job),
                preset_model_id=job.preset_model_id,
                preset_model_version=job.preset_model_version,
                profile_status="queued",
                created_at=_now(),
            )
            self.db.add(version)
        else:
            version = version_exists
        self.db.flush()
        return version

    def _default_manager_id(self, department_id: str) -> str | None:
        users = self.db.scalars(
            select(User)
            .where(
                User.department_id == department_id,
                User.is_active.is_(True),
            )
            .order_by(User.user_id)
        ).all()
        return next(
            (
                user.user_id
                for user in users
                if user.role_definition_id == "department_manager"
                or user.role == "department_manager"
            ),
            None,
        )

    def _default_recruiter_id(self, department_id: str) -> str | None:
        users = self.db.scalars(
            select(User.user_id)
            .where(
                User.department_id == department_id,
                User.is_active.is_(True),
            )
            .order_by(User.user_id)
        ).all()
        return next(
            (user_id for user_id in users if has_permission(
                self.db, self.db.get(User, user_id), "first_interview.manage"
            )),
            None,
        )

    @staticmethod
    def _frozen_job_json(job: Job) -> dict:
        """冻结 JD 业务事实；模型版本、状态和审计信息均由具名列承担。"""
        return {
            "title": job.title,
            "department_id": job.department_id,
            "headcount": job.headcount,
            "source_document_id": job.source_document_id,
            "responsibilities": list(job.responsibilities or []),
            "qualifications": list(job.qualifications or []),
            "education_requirement": job.education_requirement,
            "major_requirement": job.major_requirement,
        }

    @staticmethod
    def _draft_text(draft: JobDraft) -> str:
        lines = [
            f"岗位名称：{draft.title}",
            f"招聘人数：{draft.headcount}" if draft.headcount else "",
            "工作职责：",
            *[
                f"{index}. {item}"
                for index, item in enumerate(draft.responsibilities or [], start=1)
            ],
            "任职资格：",
            *[
                f"{index}. {item}"
                for index, item in enumerate(draft.qualifications or [], start=1)
            ],
            (
                f"学历要求：{draft.education_requirement}"
                if draft.education_requirement
                else ""
            ),
            f"专业要求：{draft.major_requirement}" if draft.major_requirement else "",
        ]
        return "\n".join(line for line in lines if line)
