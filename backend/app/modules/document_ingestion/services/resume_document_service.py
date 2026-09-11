from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from backend.app.shared.audit import record_audit_event
from backend.app.shared.errors import BusinessRuleError
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.job_identity import normalize_job_title
from types import SimpleNamespace

from backend.app.models.entities import Application, Candidate, CandidateProfile, Department, Job, ResumeSubmission, SourceDocument, User
from backend.app.modules.auth.public import AuthorizationService, assert_business_action, can_business_action
from backend.app.modules.applications.public import is_recruitment_in_progress
from backend.app.modules.candidates.public import CandidateRoutingService
from backend.app.modules.candidates.public import CandidateIntakeProcessService
from backend.app.modules.candidates.public import CandidateIntakeService, CandidateResumeRebuildService, ResumeReplacementService
from backend.app.modules.candidates.public import ResumeSubmissionStatus
from backend.app.storage.object_store import ObjectStore

from .document_upload_service import DocumentUploadService


def _now() -> datetime:
    """生成与数据库无时区字段一致的当前 UTC 时间。"""
    return datetime.now(UTC).replace(tzinfo=None)


class ResumeDocumentService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.uploads = DocumentUploadService(db)

    def list_active_job_options(self, user: User) -> list[dict[str, str]]:
        rows = self.db.execute(
            select(Job, Department)
            .join(Department, Department.department_id == Job.department_id)
            .where(
                Job.status == "open",
                Job.deleted_at.is_(None),
                Job.hiring_manager_id.is_not(None),
                Job.department_recruiter_id.is_not(None),
                Department.deleted_at.is_(None),
            )
            .order_by(Department.name, Job.title, Job.job_id)
        ).all()
        options: dict[tuple[str, str], dict[str, str]] = {}
        for job, department in rows:
            if not can_business_action(self.db, user, "resume.upload", department_id=job.department_id):
                continue
            key = (job.department_id, normalize_job_title(job.title))
            options.setdefault(
                key,
                {
                    "job_id": job.job_id,
                    "title": job.title,
                    "department_id": job.department_id,
                    "department_name": department.name,
                    "status": job.status,
                },
            )
        return list(options.values())

    def decide_duplicate(self, submission_id: str, user: User, decision: str) -> ResumeSubmission:
        submission = self.get_submission(submission_id, user)
        AuthorizationService(self.db).require_resume_submission_action(
            user,
            submission,
            "replace_duplicate_resume"
            if decision == "replace_resume"
            else "discard_submission",
        )
        application_ids = ResumeReplacementService(self.db).decide(
            submission=submission, user=user, decision=decision
        )
        submission.review_context_json = {
            **dict(submission.review_context_json or {}),
            "duplicateDecisionApplicationIds": application_ids,
        }
        return submission

    def duplicate_candidate_options(self, submission_id: str, user: User) -> dict:
        """返回“多个可能同一人”分支可由 HR 选择的候选项。"""
        submission = self.get_submission(submission_id, user)
        AuthorizationService(self.db).require_resume_submission_material_view(user, submission)
        metadata = dict((submission.review_context_json or {}).get("identityMatch") or {})
        review_context = dict(submission.review_context_json or {})
        # 新任务的重复候选人事实写在 review_context_json。
        identity_match = dict(review_context.get("identityMatch") or metadata or {})
        if (
            submission.status != ResumeSubmissionStatus.REVIEW_REQUIRED.value
            or str(getattr(submission, "review_kind", None) or metadata.get("review_kind") or "") not in {"duplicate", "duplicate_ambiguous"}
            or identity_match.get("status") != "ambiguous"
        ):
            raise BusinessRuleError(status_code=409, detail="当前简历不需要选择重复候选人")
        allowed_ids = [str(item) for item in identity_match.get("candidateIds") or identity_match.get("candidate_ids") or [] if item]
        candidates: list[dict] = []
        intake_service = CandidateIntakeService(self.db)
        for candidate_id in allowed_ids:
            candidate = self.db.get(Candidate, candidate_id)
            if candidate is None:
                continue
            profile = self.db.get(CandidateProfile, candidate.candidate_id)
            visible_applications = [
                application
                for application, _, _ in intake_service.visible_application_rows(
                    user=user,
                    candidate_id=candidate.candidate_id,
                )
            ]
            # 重复候选人选项会公开姓名、学校和专业；没有任何可见申请时也不能
            # 通过该入口暴露其身份或申请数量。
            if not visible_applications:
                continue
            active_count = sum(
                is_recruitment_in_progress(item.status)
                for item in visible_applications
            )
            candidates.append({
                "candidate_id": candidate.candidate_id,
                "display_name": candidate.display_name,
                "school": profile.school if profile is not None else "",
                "major": profile.major if profile is not None else "",
                "application_count": len(visible_applications),
                "active_application_count": active_count,
                "can_replace": active_count == 0,
            })
        return {"submission_id": submission.resume_submission_id, "candidates": candidates}

    def _delete_unused_placeholder(self, candidate: Candidate | None, submission: ResumeSubmission) -> None:
        """合并或放弃多候选人重复简历后，清除上传时生成且再无引用的占位 Candidate。"""
        if candidate is None or candidate.candidate_id == submission.candidate_id:
            return
        if candidate.current_resume_submission_id != submission.resume_submission_id:
            return
        if self.db.get(CandidateProfile, candidate.candidate_id) is not None:
            return
        has_application = self.db.scalar(
            select(Application.application_id)
            .where(Application.candidate_id == candidate.candidate_id)
            .limit(1)
        )
        if has_application is None:
            candidate.current_resume_submission_id = None
            candidate.current_resume_profile_id = None
            self.db.delete(candidate)

    def resolve_ambiguous_duplicate(
        self,
        submission_id: str,
        user: User,
        *,
        decision: str,
        target_candidate_id: str | None = None,
    ) -> tuple[ResumeSubmission, str | None, list[str]]:
        """执行多个候选人匹配的人工决定；目标候选人必须来自后端冻结的候选集合。"""
        submission = self.get_submission(submission_id, user)
        AuthorizationService(self.db).require_resume_submission_action(
            user,
            submission,
            "discard_submission"
            if decision == "discard_submission"
            else "resolve_duplicate_candidates",
        )
        metadata = dict((submission.review_context_json or {}).get("identityMatch") or {})
        review_context = dict(submission.review_context_json or {})
        # 新任务的重复候选人事实写在 review_context_json。
        identity_match = dict(review_context.get("identityMatch") or metadata or {})
        if (
            submission.status != ResumeSubmissionStatus.REVIEW_REQUIRED.value
            or str(getattr(submission, "review_kind", None) or metadata.get("review_kind") or "") not in {"duplicate", "duplicate_ambiguous"}
            or identity_match.get("status") != "ambiguous"
        ):
            raise BusinessRuleError(status_code=409, detail="当前简历不处于多候选人重复确认状态")
        placeholder = self.db.get(Candidate, submission.candidate_id) if submission.candidate_id else None
        document = self.get_document_for_submission(submission)
        allowed_ids = {str(item) for item in identity_match.get("candidateIds") or identity_match.get("candidate_ids") or [] if item}
        application_ids: list[str] = []
        next_workflow_id: str | None = None

        if decision == "discard_submission":
            CandidateIntakeProcessService.complete_submission(
                submission,
                document=document,
                candidate=placeholder,
                now=_now(),
            )
            submission.candidate_id = None
            self._delete_unused_placeholder(placeholder, submission)
        elif decision == "merge_into_candidate":
            if not target_candidate_id or target_candidate_id not in allowed_ids:
                raise BusinessRuleError(status_code=422, detail="请选择系统识别出的候选人")
            target = self.db.get(Candidate, target_candidate_id)
            if target is None:
                raise BusinessRuleError(status_code=409, detail="所选候选人已不存在，请刷新后重试")
            submission.candidate_id = target.candidate_id
            application_ids = ResumeReplacementService(self.db).decide(
                submission=submission, user=user, decision="replace_resume"
            )
            self._delete_unused_placeholder(placeholder, submission)
        elif decision == "create_new_candidate":
            if placeholder is None:
                raise BusinessRuleError(status_code=409, detail="本次上传的候选人占位记录不存在")
            structure_ref = str(metadata.get("resume_structure_ref") or "")
            if not structure_ref:
                raise BusinessRuleError(status_code=409, detail="简历结构化结果不存在")
            structure = ObjectStore().read_json(
                structure_ref,
                f"documents/{document.source_document_id}/submissions/{submission.resume_submission_id}/resume_structure_v2.json",
            )
            if not isinstance(structure, dict) or not structure.get("schema_version"):
                raise BusinessRuleError(status_code=409, detail="已保存的结构化结果无效，无法创建候选人")
            profile = CandidateIntakeService(self.db).persist_profile(
                candidate=placeholder,
                submission=submission,
                document=document,
                structure_result=structure,
                metadata=SimpleNamespace(**dict(metadata.get("resume_metadata") or {})),
            )
            CandidateIntakeProcessService.complete_submission(
                submission, document=document, candidate=placeholder, now=_now()
            )
            run = CandidateRoutingService(self.db).enqueue_submission(
                submission, triggered_by=user.user_id
            )
            next_workflow_id = run.workflow_run_id
            CandidateIntakeProcessService.activate_candidate(placeholder, now=_now())
        else:
            raise BusinessRuleError(status_code=422, detail="不支持的重复候选人处理决定")

        submission.review_context_json = {
            **dict(submission.review_context_json or {}),
            "duplicateResolution": {
                "decision": decision,
                "resolvedCandidateId": target_candidate_id if decision == "merge_into_candidate" else None,
            },
        }
        record_audit_event(
            self.db,
            actor=user,
            action="candidate.resume.ambiguous_duplicate_resolved",
            target_type="resume_submission",
            target_id=submission.resume_submission_id,
            summary="人工处理多个可能重复候选人",
            details={
                "decision": decision,
                "targetCandidateId": target_candidate_id,
                "applicationIds": application_ids,
                "nextWorkflowRunId": next_workflow_id,
            },
            workflow_run_id=next_workflow_id,
        )
        return submission, next_workflow_id, application_ids
    def intake_view(self, submission_id: str, user: User) -> dict:
        submission = self.get_submission(submission_id, user)
        return CandidateIntakeService(self.db).intake_view(submission, user=user)

    def confirm_structure(self, submission_id: str, user: User) -> tuple[ResumeSubmission, str | None, list[str]]:
        """拒绝历史“确认结构化”写路径，防止技术失败被错误放行到初筛。"""
        submission = self.get_submission(submission_id, user)
        AuthorizationService(self.db).require_resume_submission_action(
            user, submission, "confirm_structure"
        )
        raise BusinessRuleError(
            status_code=409,
            detail="简历结构化技术失败不能人工确认，请重新解析或重新上传简历",
        )
    def manual_routing_options(self, submission_id: str, user: User) -> dict:
        """提供当前待确认分发可用的岗位；其他待确认类型会被显式拒绝。"""
        submission = self.get_submission(submission_id, user)
        AuthorizationService(self.db).require_resume_submission_action(
            user, submission, "create_applications"
        )
        try:
            return CandidateIntakeService(self.db).manual_routing_options(
                submission=submission, user=user
            )
        except ValueError as exc:
            raise BusinessRuleError(status_code=409, detail=str(exc)) from exc
    def manually_create_applications(
        self, submission_id: str, user: User, job_ids: list[str]
    ) -> tuple[ResumeSubmission, list[str]]:
        submission = self.get_submission(submission_id, user)
        AuthorizationService(self.db).require_resume_submission_action(
            user, submission, "create_applications"
        )
        try:
            application_ids = CandidateIntakeService(self.db).manually_create_applications(
                user=user, submission=submission, job_ids=job_ids
            )
        except ValueError as exc:
            raise BusinessRuleError(status_code=409, detail=str(exc)) from exc
        record_audit_event(
            self.db, actor=user, action="candidate.application.manually_created",
            target_type="resume_submission", target_id=submission.resume_submission_id,
            summary="人工选择岗位并创建候选申请",
            details={"candidateId": submission.candidate_id, "jobIds": list(job_ids), "applicationIds": application_ids},
        )
        return submission, application_ids

    def get_submission(
        self, submission_id: str, user: User
    ) -> ResumeSubmission:
        submission = self.db.get(ResumeSubmission, submission_id)
        if submission is None:
            raise BusinessRuleError(status_code=404, detail="未找到简历导入记录")
        AuthorizationService(self.db).require_resume_submission_view(user, submission)
        # Candidate 与 createdApplicationIds 是当前业务关联；application_id 仅供历史记录审计，不在读取时回填。
        return submission

    def get_document_for_submission(
        self, submission: ResumeSubmission
    ) -> SourceDocument:
        document = self.db.get(SourceDocument, submission.source_document_id)
        if document is None or document.document_type != "resume":
            raise BusinessRuleError(status_code=404, detail="未找到简历源文件")
        return document

    def resume_pdf_path(self, submission: ResumeSubmission) -> Path:
        """为尚未创建岗位申请的简历提供原始 PDF 预览。"""
        document = self.get_document_for_submission(submission)
        if not document.object_ref:
            raise BusinessRuleError(status_code=404, detail="简历 PDF 文件不存在")
        try:
            path = ObjectStore().materialize(
                str(document.object_ref),
                f"resume-submissions/{submission.resume_submission_id}/resume.pdf",
            )
        except Exception as exc:
            raise BusinessRuleError(status_code=502, detail="读取简历 PDF 对象失败") from exc
        if not path.exists() or not path.is_file():
            raise BusinessRuleError(status_code=404, detail="简历 PDF 文件不存在")
        return path
