from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.entities import Application, Candidate, CandidateProfile, ResumeSubmission, SourceDocument, User
from backend.app.modules.auth.public import AuthorizationService
from backend.app.modules.assessment.public import enqueue_resume_rebuild_rescoring
from backend.app.shared.audit import record_audit_event
from backend.app.shared.errors import BusinessRuleError
from backend.app.storage.object_store import ObjectStore
from backend.app.modules.candidates.intake_service import CandidateIntakeService
from backend.app.modules.candidates.candidate_routing_service import CandidateRoutingService
from backend.app.modules.candidates.rebuild_service import CandidateResumeRebuildService
from backend.app.modules.candidates.intake_process_service import CandidateIntakeProcessService
from backend.app.modules.candidates.resume_submission_status import ResumeSubmissionStatus
from recruitment_ai_core.resume_structuring import resume_ir_from_structure


class ResumeReplacementService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def decide(self, *, submission: ResumeSubmission, user: User, decision: str) -> list[str]:
        review_kind = str(
            getattr(submission, "review_kind", None)
            or (getattr(submission, "review_context_json", {}) or {}).get("reviewKind")
            or ""
        )
        if submission.status != ResumeSubmissionStatus.REVIEW_REQUIRED.value or review_kind not in {"duplicate", "duplicate_match", "duplicate_blocked"}:
            raise BusinessRuleError(status_code=409, detail="当前简历不需要重复确认")
        document = self.db.get(SourceDocument, submission.source_document_id)
        placeholder = self.db.get(Candidate, submission.candidate_id) if submission.candidate_id else None
        identity_match = dict((submission.review_context_json or {}).get("identityMatch") or {})
        matched_candidate_id = str(identity_match.get("candidateId") or "")
        matched_candidate = self.db.get(Candidate, matched_candidate_id) if matched_candidate_id else None
        if document is None or placeholder is None or matched_candidate is None:
            raise BusinessRuleError(status_code=409, detail="重复确认所需的候选人或简历文件不存在")
        if decision == "discard_submission":
            CandidateIntakeProcessService.complete_submission(
                submission,
                document=document,
                candidate=placeholder,
                now=datetime.now(UTC).replace(tzinfo=None),
            )
            # 放弃的是尚未形成正式画像/申请的上传占位档案。清理前先解除 Submission
            # 引用；已有候选人始终只保存在 review_context 中，不受本操作影响。
            submission.candidate_id = None
            self._delete_unused_placeholder(placeholder)
            submission.review_context_json = {**dict(getattr(submission, "review_context_json", None) or {}), "duplicateResolution": {"decision": decision}}
            record_audit_event(
                self.db, actor=user, action="candidate.resume.duplicate_decided",
                target_type="resume_submission", target_id=submission.resume_submission_id,
                summary="确认不采用重复简历", details={"decision": decision},
            )
            return []
        if decision != "replace_resume":
            raise BusinessRuleError(status_code=422, detail="不支持的重复处理决定")
        if review_kind == "duplicate_blocked":
            raise BusinessRuleError(status_code=409, detail="该候选人仍在招聘流程中，当前只能放弃本次导入")
        candidate = self.db.scalar(
            select(Candidate).where(Candidate.candidate_id == matched_candidate.candidate_id).with_for_update()
        )
        if candidate is None:
            raise BusinessRuleError(status_code=404, detail="候选人不存在")
        AuthorizationService(self.db).require_candidate_material_scope(
            user, candidate.candidate_id
        )
        # 用户确认后才把 Submission 归属切换到已有 Candidate；此前的占位档案只是
        # 上传过程载体，不能提前改变已有候选人的当前简历。
        submission.candidate_id = candidate.candidate_id
        # 新任务的结构化事实来自具名字段。
        metadata = dict(
            getattr(submission, "structure_metadata_json", None)
            or {}
        )
        structure_ref = str(
            getattr(submission, "structure_result_ref", None)
            or ""
        )
        if not structure_ref:
            raise BusinessRuleError(status_code=409, detail="简历结构化结果不存在")
        structure = ObjectStore().read_json(structure_ref, f"documents/{document.source_document_id}/submissions/{submission.resume_submission_id}/resume_structure_v2.json")
        intake = CandidateIntakeService(self.db)
        profile = intake.persist_profile(candidate=candidate, submission=submission, document=document, structure_result=structure, metadata=SimpleNamespace(**metadata), force_new_version=True)
        # 1. 先发布新版 ResumeProfile/Submission；发布后才能让申请采用该版本。
        CandidateIntakeProcessService.complete_submission(
            submission, document=document, candidate=candidate, now=datetime.now(UTC).replace(tzinfo=None)
        )
        # 2. 所有附属 Application 只切换采用的简历版本，不改变招聘主状态。
        application_ids = CandidateResumeRebuildService(self.db).rebuild_applications(
            candidate=candidate,
            submission=submission,
            profile=profile,
            run_id=f"manual-duplicate:{submission.resume_submission_id}",
            user=user,
        )
        # 3. 已有申请重新评分；没有申请才回到岗位分发，以创建新的申请。
        if application_ids:
            enqueue_resume_rebuild_rescoring(
                self.db,
                user=user,
                application_ids=application_ids,
                workflow_run_id=f"manual-duplicate:{submission.resume_submission_id}",
            )
        else:
            CandidateRoutingService(self.db).enqueue_submission(
                submission, triggered_by=user.user_id
            )
        self._delete_unused_placeholder(placeholder)
        submission.review_context_json = {
            **dict(getattr(submission, "review_context_json", None) or {}),
            "duplicateDecision": decision,
            "rescoredApplicationIds": application_ids,
        }
        record_audit_event(
            self.db, actor=user, action="candidate.resume.duplicate_decided",
            target_type="resume_submission", target_id=submission.resume_submission_id,
            summary="确认采用重复简历并重建候选申请",
            details={"decision": decision, "candidateId": candidate.candidate_id, "applicationIds": application_ids},
        )
        return application_ids

    def _delete_unused_placeholder(self, placeholder: Candidate) -> None:
        """Delete only an upload placeholder that never became a business record."""
        if self.db.get(CandidateProfile, placeholder.candidate_id) is not None:
            return
        has_application = self.db.scalar(
            select(Application.application_id)
            .where(Application.candidate_id == placeholder.candidate_id)
            .limit(1)
        )
        if has_application is not None:
            return
        placeholder.current_resume_submission_id = None
        placeholder.current_resume_profile_id = None
        self.db.delete(placeholder)
