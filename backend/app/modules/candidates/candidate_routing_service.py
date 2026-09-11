"""候选人分发任务的入队与岗位画像完成后的唤醒逻辑。"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.infrastructure.workflow_runtime.queue import WorkflowQueue
from backend.app.models.entities import Application, Candidate, ResumeProfileRecord, ResumeSubmission, SourceDocument, WorkflowRun
from backend.app.modules.candidates.intake_process_service import CandidateIntakeProcessService, RoutingOutcomeStatus
from backend.app.modules.candidates.domain.resume_submission_state_machine import ResumeSubmissionStatus
from backend.app.modules.auth.public import AuthorizationService
from backend.app.shared.audit import record_audit_event
from backend.app.shared.errors import BusinessError
from backend.app.modules.candidates.workflows.candidate_routing_workflow import (
    INPUT_SCHEMA_VERSION,
    WORKFLOW_TYPE,
)


class CandidateRoutingService:
    """只负责候选人分发任务的冻结入队；实际业务分发由 workflow 执行。"""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.queue = WorkflowQueue(db)

    def enqueue_submission(
        self, submission: ResumeSubmission, *, triggered_by: str
    ) -> WorkflowRun:
        """把一份已结构化的简历提交冻结为独立的候选人分发任务。"""
        if not submission.candidate_id:
            raise RuntimeError("candidate_routing_candidate_missing")
        candidate = self.db.get(Candidate, submission.candidate_id)
        document = self.db.get(SourceDocument, submission.source_document_id)
        profile_id = candidate.current_resume_profile_id if candidate is not None else None
        profile = self.db.get(ResumeProfileRecord, profile_id) if profile_id else None
        if candidate is None or document is None or profile is None:
            raise RuntimeError("candidate_routing_input_missing")
        if profile.candidate_id != candidate.candidate_id:
            raise RuntimeError("candidate_routing_profile_mismatch")
        run, _ = self.queue.enqueue(
            workflow_type=WORKFLOW_TYPE,
            subject_type="resume_submission",
            subject_id=submission.resume_submission_id,
            triggered_by=triggered_by,
            input_json={
                "schemaVersion": INPUT_SCHEMA_VERSION,
                "resumeSubmissionId": submission.resume_submission_id,
                "candidateId": candidate.candidate_id,
                "resumeProfileId": profile.resume_profile_id,
                "sourceDocumentId": document.source_document_id,
                "sourceSha256": document.source_sha256,
            },
            reuse_active=True,
        )
        # 入队即发布“岗位分发处理中”结果；页面无需依赖 Candidate.status 猜测进度。
        CandidateIntakeProcessService.mark_routing(
            submission,
            status=RoutingOutcomeStatus.PROCESSING,
            now=datetime.now(UTC).replace(tzinfo=None),
        )
        return run

    def retry_submission(self, submission: ResumeSubmission, *, user) -> WorkflowRun:
        """Retry only routing; reuse the published ResumeProfile and current job set."""
        candidate = self.db.get(Candidate, submission.candidate_id) if submission.candidate_id else None
        if candidate is None or candidate.current_resume_submission_id != submission.resume_submission_id:
            raise BusinessError("candidate_routing_retry_not_current", "只能重新匹配候选人当前采用的简历", status_code=409)
        if str(submission.status) != ResumeSubmissionStatus.COMPLETED.value:
            raise BusinessError("candidate_routing_retry_resume_not_completed", "简历尚未发布，不能重新匹配岗位", status_code=409)
        current_routing_status = str(submission.routing_status or "")
        if current_routing_status not in {
            RoutingOutcomeStatus.FAILED.value,
            RoutingOutcomeStatus.MANUAL_SELECTION_AVAILABLE.value,
        }:
            raise BusinessError("candidate_routing_retry_not_ready", "当前岗位分发无需重新发起", status_code=409)
        AuthorizationService(self.db).require_resume_submission_action(
            user, submission, "retry_routing"
        )
        run = self.enqueue_submission(submission, triggered_by=user.user_id)
        CandidateIntakeProcessService.mark_routing(
            submission,
            status=RoutingOutcomeStatus.PROCESSING,
            now=datetime.now(UTC).replace(tzinfo=None),
        )
        record_audit_event(
            self.db,
            actor=user,
            action="candidate.routing.retry_requested",
            target_type="resume_submission",
            target_id=submission.resume_submission_id,
            summary="重新发起候选人岗位匹配",
            details={"candidateId": candidate.candidate_id, "workflowRunId": run.workflow_run_id},
            workflow_run_id=run.workflow_run_id,
        )
        return run

    def enqueue_waiting_for_job(self, job_id: str) -> list[str]:
        """岗位画像就绪后，唤醒等待该岗位的候选人。

        当前候选人仍以 current_resume_submission_id 为唯一入口。payload 中的
        waiting_job_ids 仅是等待原因的快照，不用于建立实体关联；重新执行时会重新
        从所有 open + profile_ready 的岗位中选择，避免漏掉同时完成的其他岗位。
        """
        candidates = self.db.scalars(
            select(Candidate).where(
                Candidate.current_resume_submission_id.is_not(None),
                Candidate.status != "archived",
            )
        ).all()
        queued: list[str] = []
        for candidate in candidates:
            submission = self.db.get(
                ResumeSubmission, candidate.current_resume_submission_id
            )
            routing = dict((submission.routing_result_json or {}) if submission else {})
            waiting_job_ids = {str(item) for item in routing.get("waiting_job_ids") or []}
            if submission is None or str(submission.routing_status or "") != "waiting_for_job_profiles" or job_id not in waiting_job_ids:
                continue
            self.enqueue_submission(submission, triggered_by=submission.uploaded_by)
            queued.append(submission.resume_submission_id)
        return queued

def resume_waiting_routing_for_job_profile(db: Session, *, job_id: str) -> list[str]:
    """岗位画像就绪事件的候选人订阅入口；仅幂等唤醒等待中的分发任务。"""
    return CandidateRoutingService(db).enqueue_waiting_for_job(job_id)
