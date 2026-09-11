from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.entities import JobDocumentImport, JobDraft


class JobDocumentImportStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    REVIEW_REQUIRED = "review_required"
    PARTIALLY_CONFIRMED = "partially_confirmed"
    COMPLETED = "completed"
    FAILED = "failed"


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class JobDocumentImportProcessService:
    """唯一允许写入岗位文件导入业务状态的服务。"""

    @staticmethod
    def by_source_document(
        db: Session, source_document_id: str, *, for_update: bool = False
    ) -> JobDocumentImport | None:
        query = select(JobDocumentImport).where(
            JobDocumentImport.source_document_id == source_document_id
        )
        if for_update:
            query = query.with_for_update()
        return db.scalar(query)

    @staticmethod
    def queue(
        import_task: JobDocumentImport,
        *,
        workflow_run_id: str | None = None,
        now: datetime | None = None,
    ) -> None:
        import_task.status = JobDocumentImportStatus.QUEUED.value
        import_task.review_kind = None
        import_task.failure_kind = None
        import_task.error_message = None
        import_task.recovery_code = None
        import_task.recovery_context_json = {}
        import_task.workflow_run_id = workflow_run_id
        import_task.completed_at = None
        import_task.updated_at = now or _now()

    @staticmethod
    def start(import_task: JobDocumentImport, *, now: datetime | None = None) -> None:
        import_task.status = JobDocumentImportStatus.PROCESSING.value
        import_task.review_kind = None
        import_task.failure_kind = None
        import_task.error_message = None
        import_task.recovery_code = None
        import_task.recovery_context_json = {}
        import_task.completed_at = None
        import_task.updated_at = now or _now()

    @staticmethod
    def require_review(
        import_task: JobDocumentImport,
        *,
        review_kind: str,
        reason: str | None,
        recovery_code: str,
        recovery_context: dict | None = None,
        now: datetime | None = None,
    ) -> None:
        import_task.status = JobDocumentImportStatus.REVIEW_REQUIRED.value
        import_task.review_kind = review_kind
        import_task.failure_kind = None
        import_task.error_message = reason
        import_task.recovery_code = recovery_code
        import_task.recovery_context_json = dict(recovery_context or {})
        import_task.completed_at = None
        import_task.updated_at = now or _now()

    @staticmethod
    def fail(
        import_task: JobDocumentImport,
        *,
        failure_kind: str,
        reason: str,
        recovery_code: str,
        recovery_context: dict | None = None,
        retrying: bool = False,
        now: datetime | None = None,
    ) -> None:
        import_task.status = (
            JobDocumentImportStatus.QUEUED.value
            if retrying
            else JobDocumentImportStatus.FAILED.value
        )
        import_task.review_kind = None
        import_task.failure_kind = failure_kind
        import_task.error_message = reason[:2000]
        import_task.recovery_code = recovery_code
        import_task.recovery_context_json = dict(recovery_context or {})
        import_task.completed_at = None
        import_task.updated_at = now or _now()

    @staticmethod
    def sync_drafts(
        import_task: JobDocumentImport,
        drafts: Iterable[JobDraft],
        *,
        now: datetime | None = None,
    ) -> None:
        rows = list(drafts)
        pending = any(item.status == "draft" for item in rows)
        confirmed = any(item.status == "confirmed" for item in rows)
        timestamp = now or _now()
        if pending:
            import_task.status = (
                JobDocumentImportStatus.PARTIALLY_CONFIRMED.value
                if confirmed
                else JobDocumentImportStatus.REVIEW_REQUIRED.value
            )
            import_task.review_kind = "job_drafts"
            import_task.completed_at = None
        else:
            import_task.status = JobDocumentImportStatus.COMPLETED.value
            import_task.review_kind = None
            import_task.error_message = None
            import_task.recovery_code = None
            import_task.recovery_context_json = {}
            import_task.completed_at = timestamp
        import_task.failure_kind = None
        import_task.updated_at = timestamp
