"""候选人档案的软删除命令。

``Candidate.status = archived`` 是候选人聚合的软删除标记。它不会物理删除
简历、画像与评估历史，因而审计记录仍可追溯；所有业务读模型和去重逻辑则必须
将 archived 候选人排除在外。
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from backend.app.infrastructure.observability.event_contracts import ExecutionEventType, ExecutionSeverity
from backend.app.infrastructure.observability.execution_event_writer import WorkflowExecutionEventWriter
from backend.app.models.entities import Application, Candidate, ResumeSubmission, Task, User, WorkflowRun, WorkflowStepCheckpoint
from backend.app.modules.candidates.domain.candidate_lifecycle_state_machine import CandidateLifecycleStatus, transition_candidate_lifecycle
from backend.app.shared.workflows import StepStatus, WorkflowRunStatus
from backend.app.shared.workflows.status_contracts import (
    ensure_step_transition,
    ensure_workflow_transition,
)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class CandidateDeletionService:
    """在一个 CommandRunner 事务中归档 Candidate 及其运行中业务。"""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.execution_events = WorkflowExecutionEventWriter()

    def archive(self, *, candidate: Candidate, actor: User) -> dict[str, object]:
        """软删除候选人，并取消其未完成申请和后台任务。

        ``SourceDocument``、``ResumeSubmission`` 和画像版本保留为审计证据；业务
        列表、身份匹配和文件去重会统一排除 archived Candidate，因此同一 PDF 可
        重新上传并创建新的 Candidate。
        """
        if candidate.status == CandidateLifecycleStatus.ARCHIVED.value:
            return {"candidateId": candidate.candidate_id, "deleted": True, "alreadyDeleted": True}

        now = _now()
        submission_ids = list(self.db.scalars(
            select(ResumeSubmission.resume_submission_id)
            .where(ResumeSubmission.candidate_id == candidate.candidate_id)
            .with_for_update()
        ))
        applications = list(self.db.scalars(
            select(Application).where(
                Application.candidate_id == candidate.candidate_id,
                Application.deleted_at.is_(None),
            ).with_for_update()
        ))
        application_ids = [item.application_id for item in applications]

        # 1. Candidate 是简历处理页的聚合根，通过状态机归档长期业务主体。
        transition_candidate_lifecycle(candidate, CandidateLifecycleStatus.ARCHIVED)
        candidate.updated_at = now

        # 2. 附属 Application 不应继续出现在招聘流程；历史行保留审计。
        for application in applications:
            application.deleted_at = now
            application.deleted_by_user_id = actor.user_id
            application.updated_at = now
        if application_ids:
            self.db.query(Task).filter(
                Task.application_id.in_(application_ids),
                Task.status.in_(("pending", "in_progress")),
            ).update({"status": "done", "completed_at": now, "updated_at": now}, synchronize_session=False)

        # 3. 取消简历导入/岗位分发及申请阶段的活跃 Workflow，并同步取消检查点。
        predicates = []
        if submission_ids:
            predicates.append(and_(
                WorkflowRun.subject_type == "resume_submission",
                WorkflowRun.subject_id.in_(submission_ids),
            ))
        if application_ids:
            predicates.append(WorkflowRun.application_id.in_(application_ids))
        runs = list(self.db.scalars(
            select(WorkflowRun).where(
                or_(*predicates), WorkflowRun.status.in_(("pending", "running")),
            ).with_for_update()
        )) if predicates else []
        for run in runs:
            self._cancel_run(run, now)

        self.db.flush()
        return {
            "candidateId": candidate.candidate_id,
            "deleted": True,
            "alreadyDeleted": False,
            "applicationCount": len(application_ids),
            "cancelledWorkflowCount": len(runs),
        }

    def _cancel_run(self, run: WorkflowRun, now: datetime) -> None:
        """取消一个运行中的任务，防止已取得租约的旧 Step 发布结果。"""
        ensure_workflow_transition(run.status, WorkflowRunStatus.CANCELLED)
        run.status = WorkflowRunStatus.CANCELLED.value
        run.completed_at = now
        run.available_at = None
        run.error_message = "候选人档案已删除，后台任务已取消"
        run.lease_owner = None
        run.lease_expires_at = None
        run.heartbeat_at = now
        run.updated_at = now
        for checkpoint in self.db.scalars(
            select(WorkflowStepCheckpoint)
            .where(WorkflowStepCheckpoint.workflow_run_id == run.workflow_run_id)
            .with_for_update()
        ):
            if checkpoint.status in {
                StepStatus.PENDING.value, StepStatus.RUNNING.value,
                StepStatus.RETRY_WAIT.value, StepStatus.ACTIVITY_RETRY_WAIT.value,
                StepStatus.WAITING_EXTERNAL.value, StepStatus.BLOCKED.value,
            }:
                ensure_step_transition(checkpoint.status, StepStatus.CANCELLED)
                checkpoint.status = StepStatus.CANCELLED.value
                checkpoint.completed_at = now
                checkpoint.next_attempt_at = None
                checkpoint.lease_owner = None
                checkpoint.lease_expires_at = None
                checkpoint.updated_at = now
        self.execution_events.append(
            self.db, run=run, event_type=ExecutionEventType.WORKFLOW_CANCELLED,
            severity=ExecutionSeverity.WARNING, error_code="candidate_archived",
        )
