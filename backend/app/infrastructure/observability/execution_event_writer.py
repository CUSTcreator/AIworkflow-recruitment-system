"""执行时间线的事务内写入器。"""
from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session
from backend.app.db.session import SessionLocal

from backend.app.infrastructure.observability.event_contracts import (
    ExecutionEventType,
    ExecutionSeverity,
    public_message,
    safe_diagnostics,
)
logger = logging.getLogger(__name__)

from backend.app.models.entities import (
    Application,
    ResumeSubmission,
    WorkflowExecutionEvent,
    WorkflowRun,
    WorkflowStepCheckpoint,
)


class WorkflowExecutionEventWriter:
    """将状态变更与检查点/业务发布放进同一个数据库事务。"""

    def append(
        self,
        db: Session,
        *,
        run: WorkflowRun,
        event_type: ExecutionEventType,
        checkpoint: WorkflowStepCheckpoint | None = None,
        severity: ExecutionSeverity = ExecutionSeverity.INFO,
        error_category: str | None = None,
        error_code: str | None = None,
        diagnostics: dict[str, Any] | None = None,
    ) -> WorkflowExecutionEvent:
        application_id = run.application_id
        candidate_id: str | None = None
        resume_submission_id: str | None = None
        if application_id:
            application = db.get(Application, application_id)
            candidate_id = application.candidate_id if application is not None else None
        elif run.subject_type == "resume_submission" and run.subject_id:
            submission = db.get(ResumeSubmission, run.subject_id)
            resume_submission_id = run.subject_id
            candidate_id = submission.candidate_id if submission is not None else None
        event = WorkflowExecutionEvent(
            execution_event_id=f"WFE_{uuid4().hex[:24].upper()}",
            workflow_run_id=run.workflow_run_id,
            application_id=application_id,
            candidate_id=candidate_id,
            resume_submission_id=resume_submission_id,
            step_name=checkpoint.step_name if checkpoint else None,
            event_type=event_type.value,
            severity=severity.value,
            request_id=run.request_id,
            external_request_id=(checkpoint.external_request_id if checkpoint else None),
            attempt_count=(checkpoint.attempt_count if checkpoint else None),
            poll_count=(checkpoint.poll_count if checkpoint else None),
            error_category=error_category,
            error_code=error_code,
            public_message=public_message(event_type, step_name=(checkpoint.step_name if checkpoint else None)),
            diagnostic_fields=safe_diagnostics(diagnostics),
        )
        db.add(event)
        return event
