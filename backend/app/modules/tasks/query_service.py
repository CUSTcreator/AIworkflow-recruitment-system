from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import case, false, func, or_, select
from sqlalchemy.orm import Session

from backend.app.models.entities import (
    Application,
    Candidate,
    CandidateProfile,
    Job,
    ResumeSubmission,
    Task,
    User,
)
from backend.app.modules.applications.public import application_main_route, ApplicationReadModelSource
from backend.app.modules.tasks.schemas import TaskListItem, TaskListView
from backend.app.modules.document_ingestion.public import resolve_candidate_basic_info
from backend.app.modules.applications.application_recovery import application_recovery_plan
from backend.app.modules.applications.readModel.page_actions import application_recovery_actions
from backend.app.modules.auth.public import AuthorizationService, ScopeService
def _iso(value: datetime | None) -> str:
    return value.isoformat(sep=" ", timespec="seconds") if value else ""


class TaskQueryService:
    """Build the task-center read model with joined database queries."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def _assessment_task_state(self, application: Application, user: User) -> tuple[str, list[dict[str, object]], bool]:
        """Return the V2/V3 task state used only to choose the visible entry point."""
        source = ApplicationReadModelSource(self.db)
        second = source.latest_workflow_run(application.application_id, "post_second_scoring_workflow")
        first = source.latest_workflow_run(application.application_id, "post_first_scoring_workflow")
        workflow = second if application.status in {"second_interview_evaluation", "final_review"} or second else first
        code = str(application.recovery_code or "")
        plan = application_recovery_plan(code) if code.startswith("post_") else None
        if plan is None and workflow is not None and workflow.status == "blocked":
            plan = application_recovery_plan(
                "post_second_scoring_retryable" if workflow.workflow_type == "post_second_scoring_workflow"
                else "post_first_scoring_retryable"
            )
        actions = application_recovery_actions(
            self.db,
            user,
            application,
            list(plan.actions) if plan else [],
            application_recovery_code=code,
        )
        status = {
            "pending": "queued",
            "running": "running",
            "blocked": "review_required",
            "failed": "failed",
            "completed": "completed",
        }.get(str(workflow.status), "idle") if workflow else "idle"
        if plan and plan.code.endswith("review_required"):
            status = "review_required"
        # Recovery tasks must be handled from the recruitment-flow result dialog.
        workbench_available = not (
            application.status in {"hr_second_review", "final_review"}
            and (status in {"queued", "running", "review_required", "failed"} or bool(actions))
        )
        return status, actions, workbench_available

    def _visible_application_filters(self, user: User):
        authorization = AuthorizationService(self.db)
        # 待办中关联的申请数据与候选列表使用同一可见性公式。
        if not authorization.access_context(user).is_active:
            return (false(),)
        scope = ScopeService(authorization).data_scope(user)
        if scope.business_scope == "organization":
            return ()
        return (Application.department_id == scope.department_id,)

    def my_tasks(
        self,
        user: User,
        *,
        page: int,
        page_size: int,
        keyword: str | None,
        job_id: str | None,
        sort_by: str,
        sort_order: str,
    ) -> TaskListView:
        now = datetime.now(UTC).replace(tzinfo=None)
        latest_score = ApplicationReadModelSource.latest_score_scalar(
            Application.application_id
        )
        query = (
            select(
                Task,
                Application,
                Candidate,
                Job,
                ResumeSubmission,
                latest_score.label("current_score"),
            )
            .join(Application, Application.application_id == Task.application_id)
            .join(Candidate, Candidate.candidate_id == Application.candidate_id)
            .join(Job, Job.job_id == Application.job_id)
            .outerjoin(
                ResumeSubmission,
                ResumeSubmission.resume_submission_id
                == Candidate.current_resume_submission_id,
            )
            .where(
                Application.deleted_at.is_(None),
                Task.assignee_user_id == user.user_id,
                Task.status != "done",
                Task.task_type != "run_scoring",
                *self._visible_application_filters(user),
            )
        )
        if keyword and keyword.strip():
            pattern = f"%{keyword.strip()}%"
            query = query.where(
                or_(
                    Candidate.display_name.ilike(pattern),
                    Job.title.ilike(pattern),
                    Application.application_id.ilike(pattern),
                )
            )
        if job_id:
            query = query.where(Application.job_id == job_id)
        total = int(
            self.db.scalar(select(func.count()).select_from(query.subquery())) or 0
        )
        overdue_count = int(
            self.db.scalar(
                select(func.count()).select_from(
                    query.where(
                        Task.status != "in_progress",
                        Task.due_at.is_not(None),
                        Task.due_at < now,
                    ).subquery()
                )
            )
            or 0
        )
        ascending = sort_order == "asc"
        if sort_by == "currentScore":
            score_order = latest_score.asc() if ascending else latest_score.desc()
            order_by = (
                case((latest_score.is_(None), 1), else_=0).asc(),
                score_order,
                Task.created_at.asc(),
            )
        elif sort_by == "dueAt":
            due_order = Task.due_at.asc() if ascending else Task.due_at.desc()
            order_by = (
                case((Task.due_at.is_(None), 1), else_=0).asc(),
                due_order,
                Task.created_at.asc(),
            )
        else:
            order_by = (
                case(
                    (Task.status == "in_progress", 2),
                    (Task.due_at.is_(None), 1),
                    else_=0,
                ).asc(),
                Task.due_at.asc(),
                Task.created_at.asc(),
            )
        rows = self.db.execute(
            query.order_by(*order_by)
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        items: list[TaskListItem] = []
        for task, application, candidate, job, current_submission, current_score in rows:
            basic_info = resolve_candidate_basic_info(
                self.db.get(CandidateProfile, candidate.candidate_id),
                resume_text=current_submission.parsed_text if current_submission else "",
            )
            assessment_status, recovery_actions, workbench_available = self._assessment_task_state(application, user)
            items.append(
                TaskListItem(
                    taskId=task.task_id,
                    applicationId=application.application_id,
                    taskType=task.task_type,
                    title=task.title,
                    taskStatus=task.status,
                    candidateName=candidate.display_name,
                    school=basic_info.school,
                    highestDegree=basic_info.highest_degree,
                    jobTitle=job.title,
                    jobMajorRequirement=job.major_requirement or "",
                    applicationStatus=application.status,
                    mainRoute=application_main_route(
                        application.application_id,
                        application.status,
                        application.rejection_stage,
                    ),
                    dueAt="" if task.status == "in_progress" else _iso(task.due_at),
                    overdue=bool(
                        task.status != "in_progress"
                        and task.due_at
                        and task.due_at < now
                    ),
                    currentScore=(
                        float(current_score) if current_score is not None else None
                    ),
                    assessmentUpdateStatus=assessment_status,
                    recoveryActions=recovery_actions,
                    workbenchAvailable=workbench_available,
                )
            )
        return TaskListView(
            items=items,
            total=total,
            overdueCount=overdue_count,
            page=page,
            pageSize=page_size,
        )
