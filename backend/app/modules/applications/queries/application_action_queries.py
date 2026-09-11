"""Application 的受权限保护读取：部门选项、简历文件与招聘时间线。"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from backend.app.models.entities import JobVersionRecord, ResumeSubmission, User, WorkflowRun
from backend.app.modules.applications.queries.department_query_service import ApplicationDepartmentQueryService
from backend.app.modules.applications.queries.read_models import ApplicationQueryService
from backend.app.modules.applications.services.application_access_service import ApplicationAccessService
from backend.app.modules.auth.public import AuthorizationService
from backend.app.shared.errors import BusinessRuleError
from backend.app.shared.workflows.execution_timeline import WorkflowExecutionTimelineQuery


class ApplicationActionQueryService:
    """集中处理需要先校验申请可见性的辅助读取。"""

    def __init__(self, db: Session) -> None:
        self.db = db

    def departments(self) -> list[dict[str, str]]:
        return ApplicationDepartmentQueryService(self.db).list_options()

    def resume_pdf_path(self, *, user: User, application_id: str) -> Any:
        access = ApplicationAccessService(self.db)
        application = access.get_visible(user, application_id)
        AuthorizationService(self.db).require_application_material_view(user, application)
        return access.resume_pdf_path(application_id)

    def timeline(self, *, user: User, application_id: str) -> dict[str, Any]:
        ApplicationAccessService(self.db).get_visible(user, application_id)
        return ApplicationQueryService(self.db).recruitment_timeline(application_id)

    def workflow_timeline(
        self, *, user: User, application_id: str, workflow_run_id: str, limit: int
    ) -> dict[str, Any]:
        """读取申请详情选中任务的运行轨迹，并校验任务确实属于该申请/候选人。"""
        application = ApplicationAccessService(self.db).get_visible(user, application_id)
        run = self.db.get(WorkflowRun, workflow_run_id)
        if run is None:
            raise BusinessRuleError(status_code=404, detail="未找到工作流任务")
        belongs_to_application = run.application_id == application.application_id
        belongs_to_candidate = False
        belongs_to_frozen_job_version = False
        if application.candidate_id and run.subject_id:
            if run.subject_type == "candidate":
                belongs_to_candidate = run.subject_id == application.candidate_id
            elif run.subject_type == "resume_submission":
                submission = self.db.get(ResumeSubmission, run.subject_id)
                belongs_to_candidate = bool(
                    submission and submission.candidate_id == application.candidate_id
                )
        # 岗位画像 Workflow 属于 JobVersion，而非单个 Application。只有申请冻结的
        # 那一版 JD 明确记录了该 run id 时才允许查看，不能按 job_id 宽泛放行。
        job_version = self.db.get(JobVersionRecord, application.jd_version_id)
        if job_version is not None:
            belongs_to_frozen_job_version = (
                str(job_version.profile_workflow_run_id or "") == workflow_run_id
            )
        if not (
            belongs_to_application
            or belongs_to_candidate
            or belongs_to_frozen_job_version
        ):
            # 不提示真实任务归属，避免通过 ID 探测其他部门的任务。
            raise BusinessRuleError(status_code=404, detail="该申请未关联指定工作流任务")
        return {
            "items": WorkflowExecutionTimelineQuery(self.db).for_workflow_run(
                workflow_run_id=workflow_run_id, limit=limit
            )
        }
