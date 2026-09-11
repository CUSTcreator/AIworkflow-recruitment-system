from __future__ import annotations

from datetime import UTC, datetime
from sqlalchemy.orm import Session

from sqlalchemy import case, false, func, or_, select

from backend.app.modules.applications.schemas.list_schemas import ApplicationListItem, ApplicationListView
from backend.app.modules.applications.domain.application_navigation_policy import (
    application_main_route,
    application_workspace_action,
)
from backend.app.models.entities import (
    Application,
    ApplicationAssessmentVersion,
    Candidate,
    CandidateProfile,
    CandidateCapabilityProfileRecord,
    Department,
    HumanDecision,
    HardScreeningResult,
    InterviewParseResultRecord,
    Job,
    JobRequirementProfileRecord,
    JobVersionRecord,
    ResumeSubmission,
    ScoreSnapshot,
    SourceDocument,
    StageHistory,
    User,
    WorkflowRun,
)
from backend.app.modules.applications.queries.read_model_source import ApplicationReadModelSource
from backend.app.modules.document_ingestion.public import resolve_candidate_basic_info
from backend.app.modules.interviews.public import interview_parse_result_view
from backend.app.modules.auth.public import has_permission
from backend.app.modules.applications.readModel.page_actions import (
    allowed_application_actions,
    application_recovery_actions,
)
from backend.app.modules.applications.readModel.presenters import department_name
from backend.app.modules.applications.readModel.pre_screening_process import pre_screening_process_view
from backend.app.modules.applications.readModel.assessment_stage_summary import (
    assessment_stage_summaries,
    current_execution,
)
from backend.app.modules.applications.application_recovery import application_recovery_plan
from backend.app.modules.applications.domain.application_lifecycle_policy import is_application_closed
from backend.app.modules.auth.public import AuthorizationService, ScopeService
from backend.app.modules.jobs.public import (
    evaluate_job_profile_record,
    job_recovery_plan,
    missing_job_assignments,
)
from backend.app.shared.workflows.process_view import workflow_process_view


class ApplicationQueryService:
    """Read-only, page-scoped application summary queries."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.authorization = AuthorizationService(db)
        self.scope = ScopeService(self.authorization)

    @staticmethod
    def _resume_rebuild_display(
        *, current_submission: ResumeSubmission | None, adopted_submission_id: str | None,
    ) -> tuple[str, str]:
        if current_submission is None or current_submission.intake_mode == "initial":
            return "idle", ""
        if current_submission.resume_submission_id == adopted_submission_id:
            return "idle", ""
        if current_submission.status in {"review_required"}:
            return "review_required", current_submission.error_message or "新版简历待确认"
        if current_submission.status == "failed":
            return "failed", current_submission.error_message or "新版简历处理失败"
        if current_submission.status == "completed":
            return "completed", "新版简历已完成，正在切换"
        return "processing", "新版简历处理中"
    @staticmethod
    def _iso(value: datetime | None) -> str:
        """返回带 UTC 标记的时间，避免浏览器把无时区数据库值误当成本地时间。"""
        if value is None:
            return ""
        utc_value = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        return utc_value.isoformat().replace("+00:00", "Z")
    @staticmethod
    def _decision_stage(from_status: str) -> str:
        if from_status.startswith("hard_screening"):
            return "hard_screening"
        if from_status in {"submitted", "screening_running", "department_review"}:
            return "screening"
        if from_status.startswith("first_interview"):
            return "first_interview"
        if from_status == "hr_second_review":
            return "hr_review"
        if from_status.startswith("second_interview"):
            return "second_interview"
        return "final_review"

    def _count(self, query) -> int:
        return int(
            self.db.scalar(select(func.count()).select_from(query.subquery())) or 0
        )

    def recruitment_timeline(self, application_id: str) -> dict:
        """聚合候选人采用简历与申请阶段历史，供所有招聘工作台统一展示。"""
        application = self.db.get(Application, application_id)
        if application is None:
            return {"applicationId": application_id, "items": []}
        rows = self.db.scalars(select(StageHistory).where(
            StageHistory.application_id == application_id, StageHistory.effective_at.is_not(None),
        ).order_by(StageHistory.effective_at, StageHistory.created_at)).all()
        # StageHistory 同时承担业务留痕和技术流程记录。工作台只消费白名单内的业务里程碑，
        # 未命中的记录仍完整保留在数据库与执行轨迹中，避免技术重试干扰招聘人员阅读。
        items: list[dict] = []
        for row in rows:
            item = self._timeline_item(
                event_id=row.stage_history_id, action=row.action, from_status=row.from_status,
                to_status=row.to_status, note=row.note, actor_name=row.actor_name,
                effective_at=row.effective_at, timezone=row.business_timezone, recorded_at=row.created_at,
            )
            if item is not None:
                items.append(item)

        submission = self.db.get(ResumeSubmission, application.adopted_resume_submission_id) if application.adopted_resume_submission_id else None
        if submission is not None:
            resume_item = self._timeline_item(
                event_id=f"resume:{submission.resume_submission_id}", action="resume_uploaded",
                from_status="", to_status="", note="候选人已上传本次申请采用的简历。", actor_name="",
                effective_at=submission.created_at, timezone="Asia/Shanghai", recorded_at=submission.created_at,
            )
            if resume_item is not None:
                items.append(resume_item)
        items.sort(key=lambda item: (item["effectiveAt"], item["eventId"]))
        return {"applicationId": application_id, "items": items}

    @staticmethod
    def _timeline_item(*, event_id: str, action: str, from_status: str, to_status: str,
                       note: str, actor_name: str, effective_at: datetime,
                       timezone: str, recorded_at: datetime) -> dict | None:
        """将阶段历史投影为招聘人员可读的业务里程碑。

        ``StageHistory`` 也会记录入队、重试、评分发布等技术动作；它们不是招聘决策，
        因而不应显示在工作台。返回 ``None`` 表示该记录只用于审计或排障。
        """
        labels = {
            "resume_uploaded": ("resume", "简历已关联岗位"),
            "route_candidate_to_job": ("application", "已创建岗位申请"),
            "complete_hard_screening_passed": ("screening", "硬筛通过"),
            "complete_hard_screening_failed": ("screening", "硬筛不通过"),
            "complete_hard_screening_review": ("screening", "硬筛需人工复核"),
            "review_hard_screening_pass": ("screening", "硬筛人工复核通过"),
            "review_hard_screening_reject": ("screening", "硬筛人工复核不通过"),
            "complete_scoring": ("screening", "初步筛选评估已完成"),
            "approve_first_interview": ("department", "部门决定进入一面"),
            "confirm_first_guide": ("first", "一面题单已确认"),
            "continue_first_interview_manually": ("first", "一面题单已确认（人工题纲）"),
            "complete_first_interview_pass": ("first", "一面通过"),
            "complete_first_interview_reject": ("first", "一面不通过"),
            "approve_second_interview": ("hr", "HR 决定进入二面"),
            "complete_second_interview_pass": ("second", "二面通过"),
            "complete_second_interview_reject": ("second", "二面不通过"),
            "offer": ("final", "最终决策 · 进入录用"),
            "cancel_recruitment": ("final", "招聘已取消"),
        }
        stage_decisions = {
            ("department_review", "hold"): ("department", "部门决定暂缓"),
            ("department_review", "reject"): ("department", "部门决定不推进"),
            ("department_review", "manual_review"): ("department", "部门转人工复核"),
            ("hr_second_review", "hold"): ("hr", "HR 决定暂缓"),
            ("hr_second_review", "reject"): ("hr", "HR 决定不推进"),
            ("hr_second_review", "manual_review"): ("hr", "HR 转人工复核"),
            ("final_review", "reject"): ("final", "最终决策 · 不通过"),
            ("final_review", "manual_review"): ("final", "最终决策 · 转人工复核"),
        }
        kind_and_label = stage_decisions.get((from_status, action)) or labels.get(action)
        if kind_and_label is None:
            return None
        kind, label = kind_and_label
        return {"eventId": event_id, "action": action, "kind": kind, "label": label,
                "fromStatus": from_status, "toStatus": to_status, "note": note, "actorName": actor_name,
                "effectiveAt": ApplicationQueryService._iso(effective_at), "timezone": timezone,
                "recordedAt": ApplicationQueryService._iso(recorded_at)}
    @staticmethod
    def _material_status(
        submission: ResumeSubmission | None,
        document: SourceDocument | None,
    ) -> str:
        """Return the candidate-facing resume state from its sole owner."""
        if submission is not None:
            status = submission.status
            if status in {
                "queued",
                "parsing",
                "extracting",
                "completed",
                "structure_review_required",
                "failed",
            }:
                return status
        return "completed" if document is not None else "failed"

    def _visible_query(self, user: User):
        query = (
            select(Application)
            .outerjoin(Candidate, Candidate.candidate_id == Application.candidate_id)
            .outerjoin(CandidateProfile, CandidateProfile.candidate_id == Application.candidate_id)
            .join(Job, Job.job_id == Application.job_id)
            .join(Department, Department.department_id == Application.department_id)
            .outerjoin(
                ResumeSubmission,
                ResumeSubmission.resume_submission_id == Application.adopted_resume_submission_id,
            )
            .outerjoin(
                SourceDocument,
                SourceDocument.source_document_id == ResumeSubmission.source_document_id,
            )
            .where(Application.deleted_at.is_(None))
        )
        # 普通申请读取只依赖账号有效性和数据范围，不能再要求职责包原子权限。
        if not self.authorization.access_context(user).is_active:
            return query.where(false())
        scope = self.scope.data_scope(user)
        if scope.business_scope == "organization":
            return query
        return query.where(Application.department_id == scope.department_id)

    def filter_options(self, user: User) -> dict[str, list[dict[str, str]]]:
        rows = self.db.execute(
            self._visible_query(user)
            .with_only_columns(
                Application.job_id,
                Job.title,
                Application.department_id,
                Department.name,
            )
            .distinct()
            .order_by(Department.name, Job.title, Application.job_id)
        ).all()
        departments: dict[str, dict[str, str]] = {}
        jobs: list[dict[str, str]] = []
        for job_id, title, department_id, department_name_value in rows:
            departments.setdefault(
                department_id,
                {"departmentId": department_id, "name": department_name_value},
            )
            jobs.append(
                {
                    "jobId": job_id,
                    "title": title,
                    "departmentId": department_id,
                    "departmentName": department_name_value,
                }
            )
        return {"departments": list(departments.values()), "jobs": jobs}

    @staticmethod
    def _hard_screening_preview(
        result: HardScreeningResult | None,
        *,
        current_status: str,
        summary: str,
    ) -> dict:
        rule_results = (
            list(result.rule_results_json or [])
            if result is not None
            else []
        )
        counts = {"passed": 0, "failed": 0, "manual_review": 0}
        for item in rule_results:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status") or "")
            if status in counts:
                counts[status] += 1

        target_status = (
            "failed"
            if current_status == "failed"
            else "manual_review" if current_status == "manual_review" else ""
        )
        reasons: list[dict[str, str]] = []
        if target_status:
            for item in rule_results:
                if not isinstance(item, dict) or item.get("status") != target_status:
                    continue
                reason = str(item.get("reason") or "").strip()
                if not reason:
                    continue
                reasons.append(
                    {
                        "ruleName": str(item.get("name") or "硬筛条件").strip(),
                        "reason": reason,
                    }
                )
                if len(reasons) >= 3:
                    break

        if target_status and not reasons and summary.strip():
            reasons.append(
                {
                    "ruleName": "人工复核结论" if current_status == "failed" else "复核原因",
                    "reason": summary.strip(),
                }
            )
            counts[target_status] = max(1, counts[target_status])

        return {
            "totalCount": sum(counts.values()),
            "passedCount": counts["passed"],
            "failedCount": counts["failed"],
            "reviewCount": counts["manual_review"],
            "reasons": reasons,
        }

    @staticmethod
    def _has_resume_pdf_reference(document: SourceDocument | None) -> bool:
        """Only advertise a PDF endpoint when its immutable DB reference is usable.

        The download endpoint remains responsible for reading the object store and
        returning a controlled 404/502 when an external object disappears after the
        list response. Performing one remote stat call per list row would make this
        read model both slow and dependent on MinIO availability.
        """

        if document is None or document.document_type != "resume":
            return False
        return bool(
            str(document.object_ref or "").strip()
            and str(document.content_type or "").casefold() == "application/pdf"
        )

    def list_applications(
        self,
        user: User,
        *,
        page: int,
        page_size: int,
        status: str | None = None,
        group: str | None = None,
        job_id: str | None = None,
        department_id: str | None = None,
        hard_screening_status: str | None = None,
        highest_degree: str | None = None,
        major_keyword: str | None = None,
        minimum_score: float | None = None,
        overdue_only: bool = False,
        attention_only: bool = False,
        submitted_from: datetime | None = None,
        submitted_to: datetime | None = None,
        sort_by: str = "submittedAt",
        sort_order: str = "desc",
        keyword: str | None = None,
    ) -> ApplicationListView:
        query = self._visible_query(user)
        if status:
            query = query.where(Application.status == status)
        if job_id:
            query = query.where(Application.job_id == job_id)
        if department_id:
            query = query.where(Application.department_id == department_id)
        if highest_degree:
            query = query.where(CandidateProfile.highest_degree == highest_degree)
        if major_keyword and major_keyword.strip():
            query = query.where(
                CandidateProfile.major.ilike(f"%{major_keyword.strip()}%")
            )
        latest_score = ApplicationReadModelSource.latest_score_scalar(
            Application.application_id
        )
        if minimum_score is not None:
            query = query.where(latest_score >= minimum_score)
        if overdue_only:
            query = query.where(
                Application.due_at.is_not(None),
                Application.due_at < datetime.now(UTC).replace(tzinfo=None),
                Application.status.not_in(
                    ("offer_process", "closed_rejected", "closed_cancelled")
                ),
            )
        if attention_only:
            query = query.where(
                or_(
                    Application.recovery_code.is_not(None),
                    Application.status.in_(
                        (
                            "resume_processing_failed",
                            "resume_review_required",
                            "hard_screening_review",
                            "screening_failed",
                            "manual_review",
                        )
                    ),
                )
            )
        if hard_screening_status:
            # 硬筛结果有独立正式表；payload 只用于历史申请回退，不能作为当前列表筛选入口。
            hard_status = select(HardScreeningResult.status).where(
                HardScreeningResult.application_id == Application.application_id
            ).order_by(
                HardScreeningResult.updated_at.desc(),
                HardScreeningResult.created_at.desc(),
            ).limit(1).scalar_subquery()
            if hard_screening_status == "processing":
                query = query.where(hard_status.in_(("pending", "running")))
            else:
                query = query.where(hard_status == hard_screening_status)
        if submitted_from:
            query = query.where(Application.submitted_at >= submitted_from)
        if submitted_to:
            query = query.where(Application.submitted_at < submitted_to)
        if keyword and keyword.strip():
            pattern = f"%{keyword.strip()}%"
            query = query.where(
                or_(
                    Application.application_id.ilike(pattern),
                    Candidate.candidate_id.ilike(pattern),
                    Candidate.display_name.ilike(pattern),
                    CandidateProfile.current_title.ilike(pattern),
                    CandidateProfile.school.ilike(pattern),
                    CandidateProfile.major.ilike(pattern),
                    Job.title.ilike(pattern),
                    Department.name.ilike(pattern),
                    ResumeSubmission.resume_submission_id.ilike(pattern),
                    SourceDocument.original_filename.ilike(pattern),
                )
            )

        counts_query = query
        group_counts = {
            "in_progress": self._count(
                counts_query.where(
                    Application.status.not_in(("offer_process", "closed_rejected", "closed_cancelled"))
                )
            ),
            "passed": self._count(
                counts_query.where(Application.status == "offer_process")
            ),
            "rejected": self._count(
                counts_query.where(Application.status == "closed_rejected")
            ),
            "cancelled": self._count(
                counts_query.where(Application.status == "closed_cancelled")
            ),
        }
        if group == "in_progress":
            query = query.where(
                Application.status.not_in(("offer_process", "closed_rejected", "closed_cancelled"))
            )
        elif group == "passed":
            query = query.where(Application.status == "offer_process")
        elif group == "rejected":
            query = query.where(Application.status == "closed_rejected")
        elif group == "cancelled":
            query = query.where(Application.status == "closed_cancelled")

        total = self.db.scalar(select(func.count()).select_from(query.subquery())) or 0
        direction = sort_order == "asc"
        if sort_by == "currentScore":
            score_order = latest_score.asc() if direction else latest_score.desc()
            order_by = (
                case((latest_score.is_(None), 1), else_=0).asc(),
                score_order,
                Application.submitted_at.desc(),
                Application.application_id.asc(),
            )
        elif sort_by == "updatedAt":
            primary = Application.updated_at.asc() if direction else Application.updated_at.desc()
            order_by = (primary, Application.application_id.asc())
        else:
            primary = Application.submitted_at.asc() if direction else Application.submitted_at.desc()
            order_by = (primary, Application.application_id.asc())
        applications = self.db.scalars(
            query.order_by(*order_by)
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        if not applications:
            return ApplicationListView(
                items=[],
                page=page,
                pageSize=page_size,
                total=total,
                groupCounts=group_counts,
            )

        application_ids = [app.application_id for app in applications]
        candidate_ids = {app.candidate_id for app in applications if app.candidate_id}
        job_ids = {app.job_id for app in applications}
        candidates = {
            row.candidate_id: row
            for row in self.db.scalars(select(Candidate).where(Candidate.candidate_id.in_(candidate_ids))).all()
        }
        candidate_profiles = {
            row.candidate_id: row
            for row in self.db.scalars(select(CandidateProfile).where(CandidateProfile.candidate_id.in_(candidate_ids))).all()
        }
        jobs = {
            row.job_id: row
            for row in self.db.scalars(select(Job).where(Job.job_id.in_(job_ids))).all()
        }
        job_versions = {
            row.jd_version_id: row
            for row in self.db.scalars(
                select(JobVersionRecord).where(
                    JobVersionRecord.jd_version_id.in_(
                        {app.jd_version_id for app in applications}
                    )
                )
            ).all()
        }
        job_profile_run_ids = {
            str(version.profile_workflow_run_id)
            for version in job_versions.values()
            if version.profile_workflow_run_id
        }
        job_profile_runs = {
            row.workflow_run_id: row
            for row in self.db.scalars(
                select(WorkflowRun).where(
                    WorkflowRun.workflow_run_id.in_(job_profile_run_ids)
                )
            ).all()
        } if job_profile_run_ids else {}
        job_profile_processes = {
            run_id: workflow_process_view(self.db, run)
            for run_id, run in job_profile_runs.items()
        }
        active_profile_ids = {
            str(version.active_job_profile_id)
            for version in job_versions.values()
            if version.active_job_profile_id
        }
        active_job_profiles = {
            profile.job_profile_id: profile
            for profile in self.db.scalars(
                select(JobRequirementProfileRecord).where(
                    JobRequirementProfileRecord.job_profile_id.in_(active_profile_ids)
                )
            ).all()
        } if active_profile_ids else {}
        # 初筛唯一正式来源为 ApplicationAssessmentVersion。ScreeningAssessment 已从
        # 数据模型移除，列表不再为历史兼容查询不存在的旧表。
        latest_screening: dict[str, dict] = {}

        # 正式列表分数优先读取 AAV；ScoreSnapshot 仅保留历史展示回退。
        latest_assessments: dict[str, ApplicationAssessmentVersion] = {}
        latest_assessments_by_stage: dict[str, dict[str, ApplicationAssessmentVersion]] = {}
        latest_screening_assessments: dict[str, ApplicationAssessmentVersion] = {}
        assessment_rows = self.db.scalars(
            select(ApplicationAssessmentVersion)
            .where(
                ApplicationAssessmentVersion.application_id.in_(application_ids),
                ApplicationAssessmentVersion.published_at.is_not(None),
            )
            .order_by(
                ApplicationAssessmentVersion.application_id,
                ApplicationAssessmentVersion.created_at.desc(),
                ApplicationAssessmentVersion.version.desc(),
            )
        ).all()
        for row in assessment_rows:
            latest_assessments.setdefault(row.application_id, row)
            latest_assessments_by_stage.setdefault(row.application_id, {}).setdefault(
                row.stage, row
            )
            if row.stage == "screening":
                latest_screening_assessments.setdefault(row.application_id, row)
        latest_scores: dict[str, ScoreSnapshot] = {}
        score_rows = self.db.scalars(
            select(ScoreSnapshot)
            .where(ScoreSnapshot.application_id.in_(application_ids))
            .order_by(ScoreSnapshot.application_id, ScoreSnapshot.version.desc(), ScoreSnapshot.created_at.desc())
        ).all()
        for row in score_rows:
            latest_scores.setdefault(row.application_id, row)

        latest_profiles: dict[str, dict] = {}
        profile_rows = self.db.scalars(
            select(CandidateCapabilityProfileRecord)
            .where(CandidateCapabilityProfileRecord.application_id.in_(application_ids))
            .order_by(
                CandidateCapabilityProfileRecord.application_id,
                CandidateCapabilityProfileRecord.version.desc(),
                CandidateCapabilityProfileRecord.created_at.desc(),
            )
        ).all()
        for row in profile_rows:
            latest_profiles.setdefault(row.application_id, dict(row.capability_json or {}))

        latest_interview_reviews: dict[str, dict] = {}
        parse_rows = self.db.scalars(
            select(InterviewParseResultRecord)
            .where(InterviewParseResultRecord.application_id.in_(application_ids))
            .order_by(
                InterviewParseResultRecord.application_id,
                InterviewParseResultRecord.created_at.desc(),
                InterviewParseResultRecord.version.desc(),
            )
        ).all()
        for row in parse_rows:
            latest_interview_reviews.setdefault(row.application_id, interview_parse_result_view(row))

        latest_hard_screening: dict[str, HardScreeningResult] = {}
        hard_screening_rows = self.db.scalars(
            select(HardScreeningResult)
            .where(HardScreeningResult.application_id.in_(application_ids))
            .order_by(
                HardScreeningResult.application_id,
                HardScreeningResult.updated_at.desc(),
                HardScreeningResult.created_at.desc(),
            )
        ).all()
        for row in hard_screening_rows:
            latest_hard_screening.setdefault(row.application_id, row)

        latest_hard_screening_runs: dict[str, WorkflowRun] = {}
        hard_run_rows = self.db.scalars(
            select(WorkflowRun)
            .where(
                WorkflowRun.application_id.in_(application_ids),
                WorkflowRun.workflow_type == "hard_screening_workflow",
            )
            .order_by(
                WorkflowRun.application_id,
                WorkflowRun.updated_at.desc(),
                WorkflowRun.workflow_run_id.desc(),
            )
        ).all()
        for row in hard_run_rows:
            if row.application_id:
                latest_hard_screening_runs.setdefault(row.application_id, row)

        # 初筛 V1 与一面、二面后的评估一样属于 Application 级 Workflow。列表 DTO
        # 必须投影其 Run，前端才能查看初筛失败或进行中的实际步骤，而非误打开简历轨迹。
        latest_screening_runs: dict[str, WorkflowRun] = {}
        screening_run_rows = self.db.scalars(
            select(WorkflowRun)
            .where(
                WorkflowRun.application_id.in_(application_ids),
                WorkflowRun.workflow_type == "scoring_workflow",
            )
            .order_by(
                WorkflowRun.application_id,
                WorkflowRun.started_at.desc(),
                WorkflowRun.workflow_run_id.desc(),
            )
        ).all()
        for row in screening_run_rows:
            if row.application_id:
                latest_screening_runs.setdefault(row.application_id, row)

        resume_submissions: dict[str, ResumeSubmission] = {}
        resume_documents: dict[str, SourceDocument] = {}
        # 重建任务在成功前通常只有 candidate_id：先按候选人取回，再优先使用
        # Application.adopted_resume_submission_id 表示当前申请实际采用的简历；重建状态通过 Candidate 当前 Submission 推导。
        submission_rows = self.db.scalars(
            select(ResumeSubmission)
            .where(
                or_(
                    ResumeSubmission.application_id.in_(application_ids),
                    ResumeSubmission.candidate_id.in_(candidate_ids),
                )
            )
            .order_by(ResumeSubmission.updated_at.desc(), ResumeSubmission.resume_submission_id.desc())
        ).all()
        submissions_by_id = {row.resume_submission_id: row for row in submission_rows}
        direct_by_application: dict[str, ResumeSubmission] = {}
        created_by_application: dict[str, ResumeSubmission] = {}
        latest_by_candidate: dict[str, ResumeSubmission] = {}
        for row in submission_rows:
            if row.application_id:
                direct_by_application.setdefault(row.application_id, row)
            if row.candidate_id:
                latest_by_candidate.setdefault(row.candidate_id, row)
        for app in applications:
            adopted_submission_id = str(app.adopted_resume_submission_id or "")
            if adopted_submission_id and adopted_submission_id in submissions_by_id:
                resume_submissions[app.application_id] = submissions_by_id[adopted_submission_id]
            elif app.application_id in direct_by_application:
                resume_submissions[app.application_id] = direct_by_application[app.application_id]
            elif app.candidate_id and app.candidate_id in latest_by_candidate:
                resume_submissions[app.application_id] = latest_by_candidate[app.candidate_id]
        resume_document_ids = {
            row.source_document_id for row in resume_submissions.values()
        }
        if resume_document_ids:
            resume_documents = {
                row.source_document_id: row
                for row in self.db.scalars(
                    select(SourceDocument).where(
                        SourceDocument.source_document_id.in_(resume_document_ids)
                    )
                )
            }

        rejection_stages: dict[str, str] = {}
        rejection_rows = self.db.scalars(
            select(HumanDecision)
            .where(
                HumanDecision.application_id.in_(application_ids),
                HumanDecision.to_status == "closed_rejected",
            )
            .order_by(HumanDecision.created_at.desc())
        ).all()
        for row in rejection_rows:
            rejection_stages.setdefault(row.application_id, self._decision_stage(row.from_status))

        latest_interview_updates: dict[str, WorkflowRun] = {}
        latest_post_first_runs: dict[str, WorkflowRun] = {}
        latest_post_second_runs: dict[str, WorkflowRun] = {}
        update_rows = self.db.scalars(
            select(WorkflowRun)
            .where(
                WorkflowRun.application_id.in_(application_ids),
                WorkflowRun.workflow_type.in_((
                    "post_first_scoring_workflow",
                    "post_second_scoring_workflow",
                )),
            )
            # 新建的重试任务在 Worker 领取前没有 started_at；按更新时间和创建时间
            # 排序才能稳定选中“刚重试的 pending 任务”，避免旧 cancelled 任务遮蔽操作入口。
            .order_by(
                WorkflowRun.application_id,
                WorkflowRun.updated_at.desc(),
                WorkflowRun.created_at.desc(),
                WorkflowRun.workflow_run_id.desc(),
            )
        ).all()
        for row in update_rows:
            if row.application_id:
                latest_interview_updates.setdefault(row.application_id, row)
                if row.workflow_type == "post_first_scoring_workflow":
                    latest_post_first_runs.setdefault(row.application_id, row)
                elif row.workflow_type == "post_second_scoring_workflow":
                    latest_post_second_runs.setdefault(row.application_id, row)

        latest_resume_runs: dict[str, WorkflowRun] = {}
        resume_run_rows = self.db.scalars(
            select(WorkflowRun)
            .where(
                WorkflowRun.application_id.in_(application_ids),
                WorkflowRun.workflow_type == "resume_document_import_workflow",
            )
            .order_by(WorkflowRun.application_id, WorkflowRun.started_at.desc())
        ).all()
        for row in resume_run_rows:
            if row.application_id:
                latest_resume_runs.setdefault(row.application_id, row)

        now = datetime.now(UTC).replace(tzinfo=None)
        # 岗位人员配置目前由组织级系统管理员维护。页面动作权限由后端投影，
        # 前端只能据此显示入口，不能仅凭登录角色名称自行推断。
        can_configure_job = bool(
            user.is_system_admin and user.business_scope == "organization"
        )
        items: list[ApplicationListItem] = []
        for app in applications:
            candidate = candidates.get(app.candidate_id) if app.candidate_id else None
            candidate_profile = candidate_profiles.get(candidate.candidate_id) if candidate is not None else None
            current_submission = (
                submissions_by_id.get(candidate.current_resume_submission_id)
                if candidate is not None and candidate.current_resume_submission_id else None
            )
            basic_info = (
                resolve_candidate_basic_info(
                    candidate_profile,
                    resume_text=current_submission.parsed_text if current_submission else "",
                )
                if candidate is not None
                else None
            )
            rebuild_status, rebuild_message = self._resume_rebuild_display(
                current_submission=current_submission,
                adopted_submission_id=app.adopted_resume_submission_id,
            )
            # 重建过程由 Candidate 当前 ResumeSubmission 决定，而非旧 Application 的历史导入任务。
            rebuild_run = (
                self.db.scalar(select(WorkflowRun).where(
                    WorkflowRun.subject_type == "resume_submission",
                    WorkflowRun.subject_id == current_submission.resume_submission_id,
                    WorkflowRun.workflow_type == "resume_document_import_workflow",
                ).order_by(WorkflowRun.updated_at.desc(), WorkflowRun.started_at.desc()))
                if current_submission is not None else None
            )
            rebuild_process = workflow_process_view(self.db, rebuild_run) if rebuild_run is not None else None
            job = jobs[app.job_id]
            missing_assignments = (
                missing_job_assignments(
                    hiring_manager_id=job.hiring_manager_id,
                    department_recruiter_id=job.department_recruiter_id,
                )
                if job.status == "setup_pending"
                else []
            )
            job_version = job_versions.get(app.jd_version_id)
            frozen_job = (
                dict(job_version.frozen_job_json or {})
                if job_version is not None
                else {}
            )
            # 申请及其评估固定绑定投递时的 JD 版本。必须按键是否存在决定是否回退，
            # 因为旧版本的专业要求可以合法为空，不能因此展示 Job 上的新版要求。
            application_job_title = (
                frozen_job["title"] if "title" in frozen_job else job.title
            )
            application_major_requirement = (
                frozen_job["major_requirement"]
                if "major_requirement" in frozen_job
                else job.major_requirement
            )
            job_profile_run = (
                job_profile_runs.get(str(job_version.profile_workflow_run_id))
                if job_version is not None and job_version.profile_workflow_run_id
                else None
            )
            job_profile_process = (
                job_profile_processes.get(job_profile_run.workflow_run_id)
                if job_profile_run is not None
                else None
            )
            job_profile_status = str(job_version.profile_status) if job_version is not None else "not_started"
            job_profile_message = ""
            active_profile = (
                active_job_profiles.get(str(job_version.active_job_profile_id))
                if job_version is not None and job_version.active_job_profile_id
                else None
            )
            job_profile_available = evaluate_job_profile_record(
                active_profile,
                job_id=app.job_id,
                jd_version_id=app.jd_version_id,
            ).ready
            if job_profile_status == "ready" and not job_profile_available:
                # 历史空画像可能仍保留 ready 指针；页面必须按实际可评分性提示修复，
                # 不能让用户反复点击一个必然再次阻塞的 V1 重试。
                job_profile_status = "review_required"
                job_profile_message = "岗位画像缺少可评分能力，请重新生成岗位画像。"
            if job_version is not None and job_profile_status in {"failed", "review_required"}:
                if job_version.recovery_code:
                    job_profile_message = job_recovery_plan(
                        job_version.recovery_code,
                        has_profile=job_profile_available,
                    ).public_message
                elif not job_profile_message:
                    job_profile_message = (
                        "岗位能力画像需要确认后才能继续初步筛选。"
                        if job_profile_status == "review_required"
                        else "岗位画像未能完成，请到岗位管理处理。"
                    )
            hard_result = latest_hard_screening.get(app.application_id)
            hard_run = latest_hard_screening_runs.get(app.application_id)
            hard_process = workflow_process_view(self.db, hard_run) if hard_run is not None else None
            # 正式 HardScreeningResult 优先，申请列表不再依赖历史 JSON 回退。
            stored_hard_screening_status = str(
                (hard_result.status if hard_result is not None else "")
                or app.hard_screening_status
                or ""
            )
            hard_screening_summary = str(
                (hard_result.summary if hard_result is not None else "")
                or app.hard_screening_summary
                or ""
            )
            resume_submission = resume_submissions.get(app.application_id)
            resume_document = (
                resume_documents.get(resume_submission.source_document_id)
                if resume_submission
                else None
            )
            screening = latest_screening.get(app.application_id, {})
            assessment_version = latest_assessments.get(app.application_id)
            screening_version = latest_screening_assessments.get(app.application_id)
            assessment_score = dict((assessment_version.core_result_json or {}).get("score_result") or {}) if assessment_version is not None else {}
            screening_score = dict((screening_version.core_result_json or {}).get("score_result") or {}) if screening_version is not None else {}
            if assessment_version is not None:
                aav_total = assessment_score.get("total", assessment_score.get("base_score", assessment_score.get("baseScore")))
                base_total = screening_score.get("total", screening_score.get("base_score", screening_score.get("baseScore", aav_total)))
                gate = (
                    "verified" if hard_result is None or hard_result.status == "passed"
                    else "not_qualified" if hard_result.status == "failed" else "unclear"
                )
                screening = {
                    **screening,
                    "scoreStatus": "scored",
                    "baseScore": base_total,
                    "qualificationGate": gate,
                }
            score_snapshot = latest_scores.get(app.application_id)
            profile = latest_profiles.get(app.application_id, {})
            interview_review = latest_interview_reviews.get(app.application_id, {})
            interview_review_count = len(
                interview_review.get("review_segment_ids", [])
            ) + len(interview_review.get("review_records", []))
            update_run = latest_interview_updates.get(app.application_id)
            update_process = workflow_process_view(self.db, update_run) if update_run is not None else None
            post_first_run = latest_post_first_runs.get(app.application_id)
            post_second_run = latest_post_second_runs.get(app.application_id)
            post_first_process = (
                workflow_process_view(self.db, post_first_run)
                if post_first_run is not None
                else None
            )
            post_second_process = (
                workflow_process_view(self.db, post_second_run)
                if post_second_run is not None
                else None
            )
            application_closed = is_application_closed(app.status)
            if application_closed:
                # 终态申请仍保留已发布的 V1/V2/V3 结果，但不再把旧的
                # Workflow 失败投影为当前执行任务或恢复入口。
                update_process = None
                post_first_process = None
                post_second_process = None
                screening_process = None
                hard_process = None
            screening_run = latest_screening_runs.get(app.application_id)
            if not application_closed:
                screening_process = workflow_process_view(self.db, screening_run) if screening_run is not None else None
            persisted_recovery_plan = (
                application_recovery_plan(app.recovery_code)
                if app.recovery_code
                else None
            )
            pre_screening_process = pre_screening_process_view(
                application_status=str(app.status or ""),
                job_profile_status=job_profile_status,
                job_profile_available=job_profile_available,
                job_profile_message=job_profile_message,
                job_profile_workflow_run_id=(
                    job_version.profile_workflow_run_id if job_version is not None else None
                ),
                application_recovery_message=(
                    persisted_recovery_plan.public_message
                    if persisted_recovery_plan is not None
                    else ""
                ),
                hard_screening_process=hard_process,
                screening_process=screening_process,
            )
            resume_run = latest_resume_runs.get(app.application_id)
            candidate_name = (
                candidate.display_name
                if candidate is not None
                else str(
                    (resume_submission.candidate_name_override if resume_submission else "")
                    or (resume_document.original_filename if resume_document else "")
                    or "待识别候选人"
                )
            )
            # Application 只展示招聘主状态；简历处理辅助信息必须来自 Candidate 当前的
            # ResumeSubmission，不能再读取历史 Application 的旧简历临时状态 状态。
            processing_stage = ""
            submission_status = str(resume_submission.status) if resume_submission is not None else ""
            if not processing_stage:
                processing_stage = {
                    "queued": "简历任务已排队",
                    "parsing": "简历正在解析",
                    "extracting": "简历正在结构化",
                    "review_required": "简历等待确认",
                }.get(submission_status, "")

            stage_processes = {
                "screening": screening_process,
                "after_first_interview": post_first_process,
                "after_second_interview": post_second_process,
            }
            assessment_stage_views = assessment_stage_summaries(
                app.application_id,
                latest_assessments_by_stage.get(app.application_id, {}),
                stage_processes,
            )
            job_profile_execution = (
                job_profile_process
                if pre_screening_process is not None
                and str(pre_screening_process.get("status") or "") in {
                    "waiting_job_profile",
                    "job_profile_queued",
                    "job_profile_processing",
                    "job_profile_review_required",
                    "job_profile_failed",
                }
                else None
            )
            current_execution_view = current_execution(
                [
                    ("resume", "简历处理", rebuild_process),
                    ("job_profile", "岗位画像", job_profile_execution),
                    ("hard_screening", "硬筛", hard_process),
                    ("v1", "初步筛选", screening_process),
                    ("v2", "一面后评估", post_first_process),
                    ("v3", "二面后评估", post_second_process),
                ]
            )
            if application_closed:
                current_execution_view = None
            processing_error = (
                str(getattr(resume_submission, "error_message", None) or "")
                if submission_status == "failed"
                else ""
            )
            can_retry = submission_status == "failed"
            update_status = (
                {
                    "pending": "queued",
                    "running": "running",
                    "blocked": "review_required",
                    "failed": "failed",
                    "completed": "completed",
                }.get(update_run.status, "idle")
                if update_run
                else "idle"
            )
            update_stage = (
                "first"
                if update_run and update_status != "idle" and update_run.workflow_type == "post_first_scoring_workflow"
                else "second" if update_run and update_status != "idle" else None
            )
            retry_assessment_action = (
                "retry_post_first_scoring" if update_stage == "first"
                else "retry_post_second_scoring" if update_stage == "second" else None
            )
            action_candidates = (
                (["run_scoring"] if app.status == "screening_failed" else [])
                + ([retry_assessment_action] if retry_assessment_action else [])
                + ["delete_application"]
            )
            available_actions = allowed_application_actions(
                self.db, user, app, action_candidates
            )
            if application_closed:
                available_actions = []
            recovery_candidates: list[str] = []
            if (
                persisted_recovery_plan is not None
                and not str(app.recovery_code or "").startswith("first_interview_")
            ):
                # 新写路径只消费 Application 上的稳定恢复事实；Workflow 错误正文
                # 只用于执行轨迹，不再成为页面按钮判断条件。一面题单动作归属题单
                # 工作台自己的动作目录，招聘流程列表不能用申请动作目录重复投影。
                recovery_candidates.extend(persisted_recovery_plan.actions)
                if (
                    str(app.recovery_code or "")
                    == "screening_job_profile_required"
                    and not job_profile_available
                ):
                    # 画像仍不可评分时，立即重试 V1 必然再次失败；先只展示可执行
                    # 的画像修复入口。新画像发布会自动恢复本申请。
                    recovery_candidates = [
                        action
                        for action in recovery_candidates
                        if action != "run_scoring"
                    ]
            else:
                # 阻塞型 V2/V3 任务理论上会由 blocked transition 写入 Application
                # 恢复码。对迁移前或异常中断留下的旧记录，仍依据 Workflow 的稳定
                # blocked 事实补出恢复动作，避免页面只有“待确认”而没有用户出口。
                if update_run is not None and str(update_run.status or "") == "blocked":
                    recovery_candidates.extend(
                        [
                            "edit_first_interview_feedback",
                            "retry_post_first_scoring",
                        ]
                        if update_run.workflow_type == "post_first_scoring_workflow"
                        else [
                            "edit_second_interview_feedback",
                            "retry_post_second_scoring",
                        ]
                    )
                # 仅兼容 094 迁移前已存在的终态数据。新失败必须由 Transition
                # 写入 recovery_code，不能继续扩展这里的状态推导分支。
                if app.status == "hard_screening_review":
                    recovery_candidates.extend(
                        ["review_hard_screening_pass", "review_hard_screening_reject"]
                    )
                if app.status == "screening_failed":
                    error_code = screening_process.error_code if screening_process else ""
                    if error_code == "application_frozen_resume_profile_missing":
                        recovery_candidates.append("repair_resume_source")
                    elif error_code in {
                        "application_frozen_job_profile_missing",
                        "screening_job_capabilities_missing",
                        "screening_job_capability_coverage_missing",
                    }:
                        recovery_candidates.append("repair_job_profile")
                    recovery_candidates.append("run_scoring")
                if (
                    app.status == "waiting_job_profile"
                    and job_profile_available
                ) or (
                    app.status == "hard_screening_pending" and hard_run is None
                ):
                    recovery_candidates.append("retry_initial_assessment")
                if (
                    app.status == "submitted"
                    and screening_run is None
                    and job_profile_available
                ):
                    # submitted 正常情况下会在同一事务创建 V1 Workflow；没有任务
                    # 表示自动调度未完成，属于纯恢复而不是再次授予首次筛选职责。
                    recovery_candidates.append("retry_initial_assessment")
            if (
                app.status == "screening_failed"
                and not job_profile_available
                and screening_process is not None
                and screening_process.error_code in {
                    "application_frozen_job_profile_missing",
                    "screening_job_capabilities_missing",
                    "screening_job_capability_coverage_missing",
                }
            ):
                # 094 迁移前的失败没有 Application.recovery_code。对这些历史记录
                # 同样隐藏必然再次失败的 V1 重试，只保留岗位画像修复入口。
                recovery_candidates = [
                    action for action in recovery_candidates if action != "run_scoring"
                ]
            if app.status == "waiting_job_profile" and job_profile_status in {
                "failed", "review_required",
            }:
                recovery_candidates.append("repair_job_profile")
            if rebuild_status in {"failed", "review_required"}:
                recovery_candidates.append("repair_resume_source")
            recovery_actions = application_recovery_actions(
                self.db,
                user,
                app,
                list(dict.fromkeys(recovery_candidates)),
                scoring_error_code=(screening_process.error_code if screening_process else ""),
                application_recovery_code=str(app.recovery_code or ""),
            )
            if persisted_recovery_plan is not None and retry_assessment_action:
                # 恢复态的按钮只能来自 recoveryActions。否则列表会绕过确定性
                # 错误分类，再次暴露一个使用同样坏输入的无效“重试”。
                recovery_action_names = {
                    str(item.get("action") or "") for item in recovery_actions
                }
                if retry_assessment_action not in recovery_action_names:
                    available_actions = [
                        action for action in available_actions
                        if action != retry_assessment_action
                    ]
            if application_closed:
                recovery_actions = []
            can_retry_assessment_update = bool(
                retry_assessment_action
                and retry_assessment_action in available_actions
            )
            rejection_stage = str(
                app.rejection_stage
                or rejection_stages.get(app.application_id)
                or ""
            ) or None
            # 工作台入口是读取型导航，只由申请可见性（列表查询已保证）和状态决定。
            # 恢复动作可以与只读工作台并存；页面内写按钮仍由 availableActions 和
            # recoveryActions 过滤，命令执行端继续做同一套权限与负责人校验。
            workspace_action = (
                application_workspace_action(
                    app.application_id,
                    app.status,
                    rejection_stage,
                )
                if not application_closed
                else None
            )
            verification_tags: list[str] = []
            items.append(
                ApplicationListItem(
                    applicationId=app.application_id,
                    jobId=app.job_id,
                    candidateName=candidate_name,
                    anonymizedCode=(candidate.candidate_id.replace("CAND_", "C-") if candidate is not None else ""),
                    currentTitle=basic_info.current_title if basic_info else "",
                    age=basic_info.age if basic_info else None,
                    yearsOfExperience=basic_info.years_of_experience if basic_info else "",
                    school=basic_info.school if basic_info else "",
                    major=basic_info.major if basic_info else "",
                    highestDegree=basic_info.highest_degree if basic_info else "",
                    resumePdfUrl=(
                        f"/api/v1/applications/{app.application_id}/resume-pdf"
                        if self._has_resume_pdf_reference(resume_document)
                        else None
                    ),
                    jobTitle=str(application_job_title or ""),
                    jobMajorRequirement=str(application_major_requirement or ""),
                    jobConfigurationStatus=(
                        "incomplete" if missing_assignments else "complete"
                    ),
                    missingJobAssignments=missing_assignments,
                    canConfigureJob=can_configure_job,
                    resumeSubmissionId=(
                        resume_submission.resume_submission_id
                        if resume_submission
                        else None
                    ),
                    resumeFilename=(
                        resume_document.original_filename if resume_document else None
                    ),
                    documentStatus=(
                        self._material_status(resume_submission, resume_document)
                    ),
                    candidateResolved=candidate is not None,
                    processingStage=processing_stage,
                    processingError=processing_error,
                    canRetry=can_retry and not (resume_run and resume_run.status in {"pending", "running"}),
                    department=department_name(self.db, job),
                    status=app.status,
                    resumeRebuildStatus=rebuild_status,
                    resumeRebuildMessage=rebuild_message,
                    resumeRebuildProcess=rebuild_process.to_public_dict() if rebuild_process is not None else None,
                    preScreeningProcess=pre_screening_process,
                    mainRoute=application_main_route(
                        app.application_id,
                        app.status,
                        rejection_stage,
                    ),
                    workspaceAction=workspace_action,
                    rejectionStage=rejection_stage,
                    hardScreeningStatus=str(
                        stored_hard_screening_status
                        or "not_configured"
                    ),
                    hardScreeningSummary=str(
                        hard_screening_summary
                    ),
                    hardScreeningPreview=self._hard_screening_preview(
                        latest_hard_screening.get(app.application_id),
                        current_status=stored_hard_screening_status
                        or "not_configured",
                        summary=hard_screening_summary,
                    ),
                    hardScreeningProcess=hard_process.to_public_dict() if hard_process is not None else None,
                    dueAt=self._iso(app.due_at),
                    overdue=bool(app.due_at and app.due_at < now),
                    scoreStatus=screening.get("scoreStatus", "pending"),
                    screeningError=str(screening.get("summary") or "")
                    if screening.get("scoreStatus") == "failed"
                    else "",
                    screeningProcess=screening_process.to_public_dict() if screening_process is not None else None,
                    baseScore=screening.get("baseScore"),
                    currentScore=(float(assessment_score.get("total", assessment_score.get("base_score", assessment_score.get("baseScore")))) if assessment_score.get("total", assessment_score.get("base_score", assessment_score.get("baseScore"))) is not None else (float(score_snapshot.score) if score_snapshot else screening.get("baseScore"))),
                    scoreStage=assessment_version.stage if assessment_version is not None else (score_snapshot.stage if score_snapshot else ("screening" if screening else None)),
                    assessmentUpdateStatus=("idle" if application_closed else update_status),
                    assessmentUpdateStage=(None if application_closed else update_stage),
                    assessmentUpdateMessage=(
                        "" if application_closed else "系统未能完成评估更新，可重新计算"
                        if update_run and update_status == "failed"
                        else "面评需要人工核对，核对后可重新计算"
                        if update_run and update_status == "review_required"
                        else "一面后评估已完成" if update_run and update_status == "completed" and update_stage == "first"
                        else "二面后评估已完成" if update_run and update_status == "completed" and update_stage == "second"
                        else ""
                    ),
                    assessmentUpdateProcess=update_process.to_public_dict() if update_process is not None else None,
                    canRetryAssessmentUpdate=(False if application_closed else can_retry_assessment_update),
                    availableActions=available_actions,
                    recoveryActions=recovery_actions,
                    assessmentStages=assessment_stage_views,
                    currentExecution=current_execution_view,
                    interviewReviewRequired=bool(
                        interview_review.get("review_required")
                    ),
                    interviewReviewCount=interview_review_count,
                    qualificationGate=str(screening.get("qualificationGate") or "unclear"),
                    submittedAt=self._iso(app.submitted_at),
                    updatedAt=self._iso(app.updated_at),
                )
            )
        return ApplicationListView(
            items=items,
            page=page,
            pageSize=page_size,
            total=total,
            groupCounts=group_counts,
        )
