"""一面查询服务：提供题纲、工作台和评估页面读取数据。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.entities import (
    ApplicationAssessmentVersion,
    CandidateProfile,
    FirstInterviewPlanVersion,
    Interview,
    InterviewGuide,
    User,
)
from backend.app.modules.applications.public import (
    ApplicationReadModelSource,
    allowed_application_actions,
    application_view,
    job_view,
)
from backend.app.modules.applications.application_recovery import application_recovery_plan
from backend.app.modules.assessment.public import (
    decision_summary as assessment_decision_summary,
    decision_summary_view as assessment_decision_summary_view,
)
from backend.app.modules.assessment.public import ScreeningQueryService
from backend.app.modules.assessment.public import (
    build_candidate_decision_overview,
    build_decision_support,
)
from backend.app.modules.candidates.public import candidate_profile_view
from backend.app.modules.interview_guides.public import InterviewGuideTemplateService
from backend.app.modules.interviews.queries.first_interview_plan_version_read_model import (
    first_interview_plan_view,
)
from backend.app.modules.interviews.first_interview_recovery import (
    first_interview_recovery_actions,
)
from backend.app.shared.errors import BusinessError


class FirstInterviewQueryService:
    """一面页面读模型：规划阶段读 PlanVersion，执行阶段读确认后的正式题单。"""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.source = ApplicationReadModelSource(db)

    def _progress(self, application_id: str) -> dict:
        interview = self.db.get(Interview, f"INT_{application_id}_FIRST")
        if interview is None:
            return {}
        return {
            "interviewId": interview.interview_id,
            "guideId": interview.guide_id,
            "rawNotes": interview.progress_raw_notes or "",
            "questionResponses": list(interview.progress_question_responses_json or []),
            "draftVersion": int(interview.progress_version or 0),
            "updatedAt": interview.progress_updated_at.isoformat() if interview.progress_updated_at else None,
        }
    def plan(self, application_id: str, user: User) -> dict:
        result = self.workspace(application_id, user)
        result["viewSchemaVersion"] = "first_interview_plan_v3"
        return result

    def evaluation(self, application_id: str, user: User) -> dict:
        result = self.workspace(application_id, user)
        result["viewSchemaVersion"] = "first_interview_evaluation_v2"
        return result

    def workspace(self, application_id: str, user: User) -> dict:
        source = self.source.load(application_id)
        app = source.application
        candidate = source.candidate
        job = source.job
        screening_query = ScreeningQueryService(self.db)
        try:
            screening = screening_query.screening_summary(application_id, source=source)
        except BusinessError as error:
            if error.code != "screening_not_ready":
                raise
            # 题单来源异常本身就是需要用户处理的业务状态。此时仍需返回题单
            # 工作台及 recoveryActions，不能让缺失的上游展示数据先把读接口拦成 409。
            screening = {}
        if not candidate or not job:
            raise BusinessError(
                "first_interview_workspace_incomplete",
                "一面工作区缺少候选人或岗位数据",
                status_code=409,
            )

        plan_version = self._latest_plan(application_id)
        confirmed_guide = self._confirmed_guide(plan_version)
        if plan_version is not None and plan_version.status == "confirmed" and confirmed_guide:
            plan = confirmed_guide
        elif plan_version is not None:
            plan = first_interview_plan_view(plan_version)
            plan = InterviewGuideTemplateService(self.db).attach_preview(plan, job)
        else:
            # 历史数据兼容：直到旧草稿数据清理完成前仍可展示。
            plan = confirmed_guide
            if plan:
                plan = InterviewGuideTemplateService(self.db).attach_preview(plan, job)

        planning_run = self.source.latest_workflow_run(
            application_id,
            "first_interview_planning_workflow",
        )
        # 最新运行的失败/阻塞优先于历史草稿状态；否则用户会看到旧草稿却误以为
        # 本轮重生成已经完成。草稿仍保留在 plan 中，供用户编辑或人工接管。
        planning_status = (
            planning_run.status
            if planning_run is not None and planning_run.status in {"failed", "blocked"}
            else "ready"
            if plan_version is not None or plan
            else planning_run.status
            if planning_run
            else "not_started"
        )
        stored_recovery_code = str(app.recovery_code or "")
        recovery_code = (
            stored_recovery_code
            if stored_recovery_code.startswith("first_interview_")
            else "first_interview_planning_retryable"
            if planning_run is not None and planning_run.status in {"failed", "blocked"}
            else ""
        )
        recovery_plan = application_recovery_plan(recovery_code) if recovery_code else None
        recovery_actions = first_interview_recovery_actions(
            self.db,
            user,
            app,
            list(recovery_plan.actions) if recovery_plan else [],
            recovery_code=recovery_code,
        )
        progress = self._progress(application_id)
        decision_summary = self._decision_summary(application_id, plan_version)
        action_candidates: list[str] = []
        if app.status == "first_interview_planning":
            if plan_version is not None and plan_version.status == "draft":
                action_candidates.append("confirm_first_guide")
            elif planning_status == "not_started":
                action_candidates.append("run_first_interview_planning")
        elif app.status == "first_interview_scheduled":
            action_candidates.append("start_first_interview")
        elif app.status in {"first_interview_in_progress", "first_interview_evaluation"}:
            action_candidates.extend(
                ["save_first_interview_progress", "complete_first_interview"]
            )

        screening_result = screening.get("screeningResultView") or {}
        decision_support = build_decision_support(
            screening,
            plan=plan,
            progress=progress,
            decision_summary=decision_summary,
        )
        return {
            "application": application_view(self.db, app),
            "candidate": candidate_profile_view(
                candidate, self.db.get(CandidateProfile, candidate.candidate_id)
            ),
            "job": job_view(job, self.db),
            "hardScreening": screening_query.hard_screening_review(
                application_id, source=source
            ),
            "screening": screening,
            "plan": plan,
            "planningState": {
                "status": planning_status,
                "planVersionId": plan_version.plan_version_id if plan_version else None,
                "sourceAssessmentVersionId": plan_version.source_assessment_version_id if plan_version else None,
                "generationMode": (plan_version.presentation_json or {}).get("generation_mode") if plan_version else None,
                "workflowRunId": planning_run.workflow_run_id if planning_run else None,
                "error": recovery_plan.public_message if recovery_plan else None,
                "recoveryCode": recovery_code or None,
                "recoveryMessage": recovery_plan.public_message if recovery_plan else None,
            },
            "progressDraft": progress,
            "decisionSummary": assessment_decision_summary_view(self._assessment_row(application_id, plan_version)) if self._assessment_row(application_id, plan_version) is not None else None,
            "decisionOverview": build_candidate_decision_overview(
                screening_result=screening_result,
                decision_summary=decision_summary,
                decision_support=decision_support,
            ),
            "decisionSupport": decision_support,
            "evidenceIndex": screening_result.get("evidenceIndex") or {},
            "availableActions": allowed_application_actions(
                self.db, user, app, action_candidates
            ),
            "recoveryActions": recovery_actions,
            "workflowStatus": {
                "workflowRunId": planning_run.workflow_run_id if planning_run else None,
                "workflowType": "first_interview_planning_workflow",
                "runStatus": {
                    "not_started": "not_started",
                    "pending": "queued",
                    "running": "running",
                    "ready": "completed",
                    "completed": "completed",
                    "failed": "failed",
                    "blocked": "review_required",
                }.get(
                    planning_run.status if planning_run is not None else planning_status,
                    "not_started",
                ),
                "applicationStatus": app.status,
                "error": recovery_plan.public_message if recovery_plan else None,
            },
        }

    def _latest_plan(self, application_id: str) -> FirstInterviewPlanVersion | None:
        return self.db.scalars(
            select(FirstInterviewPlanVersion)
            .where(FirstInterviewPlanVersion.application_id == application_id)
            .order_by(
                FirstInterviewPlanVersion.version.desc(),
                FirstInterviewPlanVersion.created_at.desc(),
            )
        ).first()

    def _confirmed_guide(self, plan_version: FirstInterviewPlanVersion | None) -> dict | None:
        """确认后的执行题单必须精确绑定当前 PlanVersion，不能按申请取“最新”混入重做题单。"""
        if plan_version is None:
            return None
        row = self.db.scalars(
            select(InterviewGuide)
            .where(InterviewGuide.plan_version_id == plan_version.plan_version_id)
            .order_by(InterviewGuide.created_at.desc())
        ).first()
        return dict(row.content_json or {}) if row is not None else None
    def _assessment_row(
        self, application_id: str, plan_version: FirstInterviewPlanVersion | None,
    ) -> ApplicationAssessmentVersion | None:
        assessment_id = plan_version.source_assessment_version_id if plan_version else None
        row = self.db.get(ApplicationAssessmentVersion, assessment_id) if assessment_id else None
        if row is not None and row.published_at is not None:
            return row
        return self.db.scalars(
            select(ApplicationAssessmentVersion)
            .where(
                ApplicationAssessmentVersion.application_id == application_id,
                ApplicationAssessmentVersion.stage == "screening",
                ApplicationAssessmentVersion.published_at.is_not(None),
            )
            .order_by(ApplicationAssessmentVersion.version.desc(), ApplicationAssessmentVersion.created_at.desc())
        ).first()
    def _decision_summary(
        self,
        application_id: str,
        plan_version: FirstInterviewPlanVersion | None,
    ) -> dict | None:
        row = self._assessment_row(application_id, plan_version)
        return assessment_decision_summary(row) if row is not None else None
