from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.entities import Task


class TaskWriteService:
    """Single write boundary for active recruitment tasks."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def ensure_pending(
        self,
        *,
        application_id: str,
        task_type: str,
        title: str,
        assignee_user_id: str,
        assignee_role: str,
    ) -> Task:
        existing = self.db.scalar(
            select(Task).where(
                Task.application_id == application_id,
                Task.task_type == task_type,
                Task.status != "done",
            )
        )
        if existing is not None:
            return existing
        task = Task(
            task_id=f"TASK_{uuid4().hex[:12]}",
            application_id=application_id,
            task_type=task_type,
            title=title,
            status="pending",
            assignee_user_id=assignee_user_id,
            assignee_role=assignee_role,
        )
        self.db.add(task)
        return task

    def complete(self, *, application_id: str, task_type: str) -> None:
        now = datetime.now(UTC).replace(tzinfo=None)
        tasks = self.db.scalars(
            select(Task).where(
                Task.application_id == application_id,
                Task.task_type == task_type,
                Task.status != "done",
            )
        ).all()
        for task in tasks:
            task.status = "done"
            task.completed_at = now
            task.updated_at = now