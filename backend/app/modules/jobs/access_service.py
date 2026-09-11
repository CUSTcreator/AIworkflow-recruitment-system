from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.models.entities import Job, User
from backend.app.modules.auth.public import AuthorizationService, ScopeService
from backend.app.shared.errors import BusinessError


class JobAccessService:
    """Checks whether a user may view a job in the current business scope."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def assert_visible(self, user: User, job: Job) -> Job:
        authorization = AuthorizationService(self.db)
        context = authorization.access_context(user)
        # 岗位读取不依赖岗位维护职责；普通查看只受账号状态和数据范围限制。
        if not context.is_active:
            raise BusinessError(
                "job_view_forbidden", "当前账号已停用", status_code=403
            )
        if not ScopeService(authorization).data_scope(user).allows_department(job.department_id):
            raise BusinessError(
                "job_scope_forbidden",
                "岗位不在当前账号的数据范围内",
                status_code=403,
            )
        return job
