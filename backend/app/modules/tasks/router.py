"""待办 HTTP 适配层：返回当前用户可处理的任务列表。"""

from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.app.modules.auth.public import get_current_user
from backend.app.db.session import get_db
from backend.app.models.entities import User
from backend.app.modules.tasks.query_service import TaskQueryService
from backend.app.modules.tasks.schemas import TaskListView


router = APIRouter(tags=["tasks"])


@router.get("/tasks/my", response_model=TaskListView)
def my_tasks(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, alias="pageSize", ge=1, le=100),
    keyword: str | None = Query(default=None, max_length=100),
    job_id: str | None = Query(default=None, alias="jobId"),
    sort_by: Literal["priority", "dueAt", "currentScore"] = Query(
        default="priority", alias="sortBy"
    ),
    sort_order: Literal["asc", "desc"] = Query(default="asc", alias="sortOrder"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return TaskQueryService(db).my_tasks(
        user,
        page=page,
        page_size=page_size,
        keyword=keyword,
        job_id=job_id,
        sort_by=sort_by,
        sort_order=sort_order,
    )
