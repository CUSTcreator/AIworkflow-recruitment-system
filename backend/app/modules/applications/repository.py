from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.db.session import Base
from backend.app.models.entities import Application


class ApplicationRepository:
    """Database access owned by the applications module."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def get_for_update(self, application_id: str) -> Application | None:
        return self.db.scalar(
            select(Application)
            .where(Application.application_id == application_id)
            .with_for_update()
        )

    def source_document_reference_count(self, document_id: str) -> int:
        total = 0
        for table_name in (
            "resume_submissions",
            "application_workspace_documents",
            "candidate_workspace_documents",
            "job_workspace_documents",
            "job_drafts",
        ):
            table = Base.metadata.tables.get(table_name)
            if table is None or "source_document_id" not in table.c:
                continue
            total += int(
                self.db.scalar(
                    select(func.count())
                    .select_from(table)
                    .where(table.c.source_document_id == document_id)
                )
                or 0
            )
        return total
