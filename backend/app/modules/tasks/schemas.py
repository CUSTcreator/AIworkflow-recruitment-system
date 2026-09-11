from __future__ import annotations

from pydantic import BaseModel, Field


class TaskListItem(BaseModel):
    taskId: str
    applicationId: str
    taskType: str
    title: str
    mainRoute: str
    taskStatus: str
    candidateName: str
    school: str = ""
    highestDegree: str = ""
    jobTitle: str
    applicationStatus: str
    dueAt: str = ""
    overdue: bool = False
    currentScore: float | None = None
    assessmentUpdateStatus: str = "idle"
    recoveryActions: list[dict[str, object]] = Field(default_factory=list)
    workbenchAvailable: bool = True


class TaskListView(BaseModel):
    items: list[TaskListItem]
    total: int
    overdueCount: int
    page: int = 1
    pageSize: int = 30
