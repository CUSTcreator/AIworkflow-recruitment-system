from __future__ import annotations

from typing import Any

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.entities import (
    Application,
    ApplicationAssessmentVersion,
    CandidateProfile,
    HardScreeningResult,
    InterviewParseResultRecord,
    JobRequirementProfileRecord,
    ResumeProfileRecord,
    WorkflowRun,
)
from backend.app.models.entities import User
from backend.app.modules.applications.public import (
    ApplicationReadModelSource,
    allowed_application_actions,
    application_view,
    job_view,
)
from backend.app.modules.applications.application_recovery import application_recovery_plan
from backend.app.modules.applications.readModel.page_actions import application_recovery_actions
from backend.app.modules.assessment.schemas.view_schemas import EvidenceDetailView, ScoringStatusView
from backend.app.modules.assessment.queries.decision_summary import DecisionSummaryQueryService
from backend.app.modules.assessment.readModel.presenters import screening_summary_view
from backend.app.modules.applications.public import ApplicationReadSource
from backend.app.modules.assessment.queries.decision_support import build_decision_support
from backend.app.modules.assessment.queries.decision_overview import build_candidate_decision_overview
from backend.app.modules.assessment.queries.assessment_version_read_model import (
    assessment_payload as assessment_version_payload,
    decision_overview as assessment_version_decision_overview,
    decision_summary as assessment_version_decision_summary,
    screening_result_view as assessment_version_screening_result_view,
)
from backend.app.modules.candidates.public import candidate_profile_view
from backend.app.shared.errors import BusinessError
from backend.app.shared.workflows.process_view import workflow_process_view


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _display_number(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value or "").strip()
    return str(int(number)) if number.is_integer() else str(number)


def _display_expected_value(value: Any) -> str:
    if isinstance(value, list):
        return "、".join(
            item for item in (str(entry).strip() for entry in value) if item
        )
    if isinstance(value, dict):
        minimum = value.get("min")
        maximum = value.get("max")
        if minimum is not None and maximum is not None:
            left, right = _display_number(minimum), _display_number(maximum)
            return left if left == right else f"{left} 至 {right}"
        if minimum is not None:
            return f"{_display_number(minimum)}及以上"
        if maximum is not None:
            return f"{_display_number(maximum)}及以下"
        return ""
    return _display_number(value)


def _hard_screening_requirement_text(item: dict[str, Any]) -> str:
    expected = _display_expected_value(item.get("expected_value"))
    operator = str(item.get("operator") or "")
    if operator == "exists":
        return expected or "必须具备"
    if operator == "degree_at_least" and expected:
        return expected if expected.endswith("及以上") else f"{expected}及以上"
    if operator == "years_at_least" and expected:
        value = expected[:-3] if expected.endswith("及以上") else expected
        return f"{value}及以上" if "年" in value else f"{value} 年及以上"
    if operator == "years_between" and expected:
        return expected if "年" in expected else f"{expected} 年"
    if operator == "contains_any" and expected:
        return f"满足任一项：{expected}"
    if operator == "contains_all" and expected:
        return f"需全部满足：{expected}"
    if operator == "not_contains_any" and expected:
        return f"不得包含：{expected}"
    return expected or "已配置要求"


def _hard_screening_review_view(source: dict[str, Any]) -> dict[str, Any]:
    """Convert the frozen hard-screening source into the V1 page DTO.

    Rule results already contain the rule name and expected value used during
    evaluation. Reading them from the assessment snapshot prevents a later job
    policy edit from rewriting the historical V1 review page.
    """
    allowed_statuses = {
        "not_configured", "pending", "running", "passed", "failed", "manual_review"
    }
    status = str(source.get("status") or "not_configured")
    if status == "processing":
        status = "running"
    if status not in allowed_statuses:
        status = "not_configured"
    requirements: list[dict[str, Any]] = []
    raw_items = source.get("items")
    if not isinstance(raw_items, list):
        raw_items = []
    for index, raw in enumerate(raw_items, start=1):
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        item_status = str(item.get("status") or "not_evaluated")
        if item_status not in {"passed", "failed", "manual_review"}:
            item_status = "not_evaluated"
        raw_quotes = item.get("source_quotes")
        if not isinstance(raw_quotes, list):
            raw_quotes = []
        quotes = [
            quote
            for quote in dict.fromkeys(
                str(value).strip() for value in raw_quotes
            )
            if quote
        ]
        requirements.append({
            "ruleId": str(item.get("rule_id") or f"hard-screening-rule-{index}"),
            "name": str(item.get("name") or f"硬筛条件 {index}"),
            "requirementText": _hard_screening_requirement_text(item),
            "status": item_status,
            "reason": str(item.get("reason") or ""),
            "reasonCode": str(item.get("reason_code") or "") or None,
            "sourceQuotes": quotes,
        })
    counts = {
        "total": len(requirements),
        "passed": sum(item["status"] == "passed" for item in requirements),
        "failed": sum(item["status"] == "failed" for item in requirements),
        "manualReview": sum(item["status"] == "manual_review" for item in requirements),
        "notEvaluated": sum(item["status"] == "not_evaluated" for item in requirements),
    }
    return {
        "resultId": str(source.get("resultId") or "") or None,
        "policyId": str(source.get("policyId") or "") or None,
        "status": status,
        "summary": str(source.get("reason") or source.get("summary") or ""),
        "counts": counts,
        "requirements": requirements,
    }


class ScreeningQueryService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.source = ApplicationReadModelSource(db)

    def _recovery_actions(self, application: Application, user: User) -> list[dict[str, object]]:
        """把 V1 失败的稳定恢复码投影成当前用户可执行的动作。"""
        code = str(application.recovery_code or "")
        if not code and application.status == "screening_failed":
            # 兼容迁移前没有 recovery_code 的历史失败申请。
            code = "screening_retryable"
        if not code:
            return []
        plan = application_recovery_plan(code)
        if plan is None:
            return []
        return application_recovery_actions(
            self.db,
            user,
            application,
            list(plan.actions),
            application_recovery_code=code,
        )

    def _latest_assessment_version(
        self, application_id: str,
    ) -> ApplicationAssessmentVersion | None:
        """取得初筛阶段最新正式版本；旧表只用于未迁移的历史申请。"""
        return self.db.scalars(
            select(ApplicationAssessmentVersion)
            .where(
                ApplicationAssessmentVersion.application_id == application_id,
                ApplicationAssessmentVersion.stage == "screening",
                ApplicationAssessmentVersion.published_at.is_not(None),
            )
            .order_by(
                ApplicationAssessmentVersion.version.desc(),
                ApplicationAssessmentVersion.created_at.desc(),
            )
        ).first()

    def _assessment_context(self, version: ApplicationAssessmentVersion) -> dict[str, dict[str, Any]]:
        """按评估版本冻结的外键读取来源，禁止再从旧评分表回填页面数据。"""
        resume = self.db.get(ResumeProfileRecord, version.resume_profile_id)
        job = self.db.get(JobRequirementProfileRecord, version.job_profile_id)
        source = dict(version.source_json or {})
        # AAV 是版本化评估的冻结边界：页面优先读取写入 source_json 的硬筛快照，
        # 不能因后来重跑硬筛而改变历史 V1/V2/V3 的展示。仅历史 AAV 没有快照时回退查询。
        hard_source = dict(source.get("hardScreening") or {})
        hard = None
        if not hard_source:
            hard = self.db.scalars(
                select(HardScreeningResult)
                .where(HardScreeningResult.application_id == version.application_id)
                .order_by(HardScreeningResult.updated_at.desc(), HardScreeningResult.created_at.desc())
            ).first()
            hard_source = {
                "resultId": hard.result_id if hard is not None else None,
                "policyId": hard.policy_id if hard is not None else None,
                "status": hard.status if hard is not None else "not_configured",
                "items": list(hard.rule_results_json or []) if hard is not None else [],
                "reason": str(hard.summary or "") if hard is not None else "",
            }
        status = str(hard_source.get("status") or "not_configured")
        return {
            "resume_profile": dict(resume.profile_data or {}) if resume is not None else {},
            "job_profile": dict(job.profile_json or {}) if job is not None else {},
            "hard_screening": {
                "resultId": hard_source.get("resultId"),
                "policyId": hard_source.get("policyId"),
                "gate": "verified" if status == "passed" else "not_qualified" if status == "failed" else "unclear",
                "status": status,
                "items": list(hard_source.get("items") or []),
                "reason": str(hard_source.get("reason") or ""),
            },
        }

    def _assessment_payload(self, version: ApplicationAssessmentVersion) -> dict[str, Any]:
        return assessment_version_payload(version, **self._assessment_context(version))

    def _assessment_result_view(self, version: ApplicationAssessmentVersion) -> dict[str, Any]:
        return assessment_version_screening_result_view(version, **self._assessment_context(version))

    def _assessment_decision_overview(self, version: ApplicationAssessmentVersion) -> dict[str, Any]:
        return assessment_version_decision_overview(version, **self._assessment_context(version))

    def _hard_screening_source(
        self,
        application: Application,
        version: ApplicationAssessmentVersion | None,
    ) -> dict[str, Any]:
        if version is not None:
            return dict(self._assessment_context(version)["hard_screening"])
        row = self.db.scalars(
            select(HardScreeningResult)
            .where(HardScreeningResult.application_id == application.application_id)
            .order_by(HardScreeningResult.updated_at.desc(), HardScreeningResult.created_at.desc())
        ).first()
        if row is None:
            return {
                "status": str(application.hard_screening_status or "not_configured"),
                "reason": str(application.hard_screening_summary or ""),
                "items": [],
            }
        return {
            "resultId": row.result_id,
            "policyId": row.policy_id,
            "status": row.status,
            "reason": row.summary or "",
            "items": list(row.rule_results_json or []),
        }

    def hard_screening_review(
        self,
        application_id: str,
        *,
        source: ApplicationReadSource | None = None,
    ) -> dict[str, Any]:
        """Project the V1-frozen hard-screening decision for downstream pages.

        Interview pages must keep showing the rules that were actually used by
        V1. They must not join the application's result with the job's current
        policy, which may have changed after screening.
        """
        source = source or self.source.load(application_id)
        return _hard_screening_review_view(
            self._hard_screening_source(
                source.application,
                self._latest_assessment_version(application_id),
            )
        )

    def _latest_assessment(
        self, application_id: str, *, source: ApplicationReadSource | None = None
    ) -> dict | None:
        version = self._latest_assessment_version(application_id)
        if version is not None:
            return self._assessment_payload(version)
        return (source or self.source.load(application_id)).assessment_payload

    def screening_summary(
        self,
        application_id: str,
        *,
        source: ApplicationReadSource | None = None,
    ) -> dict:
        version = self._latest_assessment_version(application_id)
        if version is not None:
            return self._assessment_payload(version)
        read_source = source or self.source.load(application_id)
        return screening_summary_view(read_source.assessment_payload)

    def screening_result(self, application_id: str) -> dict[str, Any]:
        if self.db.get(Application, application_id) is None:
            raise BusinessError(
                "application_not_found",
                "未找到候选申请",
                status_code=404,
            )
        version = self._latest_assessment_version(application_id)
        if version is not None:
            return self._assessment_result_view(version)
        assessment = self._latest_assessment(application_id) or {}
        view = assessment.get("screeningResultView")
        if isinstance(view, dict):
            return view

        # Historical rows created before versioned assessment storage remain readable.
        artifact = self.source.latest_artifact(application_id, "screening_result")
        if artifact and isinstance(artifact.artifact_json, dict):
            legacy_view = artifact.artifact_json.get("screening_result_view")
            if isinstance(legacy_view, dict):
                return legacy_view
        raise BusinessError(
            "screening_result_not_found",
            "该申请尚未生成初步筛选结果",
            status_code=404,
        )
    def screening_review(self, application_id: str, user: User) -> dict:
        source = self.source.load(application_id)
        app = source.application
        candidate = source.candidate
        job = source.job
        assessment_version = self._latest_assessment_version(application_id)
        assessment = self.screening_summary(application_id, source=source)
        if not candidate or not job:
            raise BusinessError(
                "screening_context_missing",
                "候选申请缺少候选人或岗位数据",
                status_code=409,
            )
        result_view = assessment.get("screeningResultView")
        if not isinstance(result_view, dict):
            raise BusinessError(
                "screening_result_not_ready",
                "初步筛选结果尚未成功生成",
                status_code=409,
            )
        decision_summary = (
            assessment_version_decision_summary(assessment_version)
            if assessment_version is not None
            else DecisionSummaryQueryService(self.db).latest(
                application_id,
                source=source,
            )
        )
        decision_support = build_decision_support(
            assessment,
            decision_summary=decision_summary,
        )
        workflow_status = self.scoring_status(application_id, user=user, source=source)
        action_candidates = {
            "submitted": ["run_scoring"],
            "screening_failed": ["run_scoring"],
            "department_review": [
                "approve_first_interview",
                "hold",
                "manual_review",
                "reject",
            ],
        }.get(app.status, [])
        return {
            "application": application_view(self.db, app),
            "candidate": candidate_profile_view(candidate, self.db.get(CandidateProfile, candidate.candidate_id)),
            "job": job_view(job, self.db),
            "hardScreening": self.hard_screening_review(application_id, source=source),
            "screeningResult": result_view,
            "decisionOverview": (
                self._assessment_decision_overview(assessment_version)
                if assessment_version is not None
                else build_candidate_decision_overview(
                    screening_result=result_view,
                    decision_summary=decision_summary,
                    decision_support=decision_support,
                )
            ),
            "decisionSupport": decision_support,
            "evidenceIndex": result_view.get("evidenceIndex") or {},
            "availableActions": allowed_application_actions(
                self.db,
                user,
                app,
                action_candidates,
            ),
            "recoveryActions": self._recovery_actions(app, user),
            "workflowStatus": workflow_status,
        }

    def evidence_detail(self, application_id: str, evidence_id: str) -> EvidenceDetailView:
        """返回可读的原文依据；主读模型仍保持脱敏。"""
        assessment_version = self._latest_assessment_version(application_id)
        if assessment_version is None:
            raise BusinessError("screening_result_not_found", "未找到初步筛选结果", status_code=404)
        # 评分页面的 evidenceIndex 会在读模型边界移除 rawText，详情接口不能
        # 再从该对象取值，否则“查看依据”只剩图片高亮。这里按正式 AAV 外键
        # 读取冻结 ResumeProfile，确保展示的句子与实际评分来源一致。
        resume = self.db.get(ResumeProfileRecord, assessment_version.resume_profile_id)
        resume_profile = dict(resume.profile_data or {}) if resume is not None else {}
        from recruitment_ai_core.screening_scoring.profile_evidence import (
            nested_source_bullets,
            nested_work_units,
        )

        for item in [*nested_source_bullets(resume_profile), *nested_work_units(resume_profile)]:
            candidate_ids = {
                str(item.get("source_bullet_id") or ""),
                str(item.get("work_unit_id") or ""),
            }
            if evidence_id not in candidate_ids:
                continue
            return EvidenceDetailView(
                evidenceId=evidence_id,
                rawText=str(item.get("raw_text") or "") or None,
                sourceLineStart=item.get("source_line_start"),
                sourceLineEnd=item.get("source_line_end"),
                sourceBulletId=str(item.get("source_bullet_id") or "") or None,
            )

        # V2/V3 的依据可能是面评断言 ID，不属于简历 evidenceIndex；从已发布
        # IPR 的 assertions 中返回 sourceQuote/text，仍只读正式冻结结果。
        parse_rows = self.db.scalars(
            select(InterviewParseResultRecord)
            .where(InterviewParseResultRecord.application_id == application_id)
            .order_by(
                InterviewParseResultRecord.created_at.desc(),
                InterviewParseResultRecord.parse_result_id.desc(),
            )
        ).all()
        for row in parse_rows:
            for assertion in row.assertions_json or []:
                if not isinstance(assertion, dict):
                    continue
                if str(assertion.get("assertionId") or "") != evidence_id:
                    continue
                quote = str(assertion.get("sourceQuote") or assertion.get("text") or "").strip()
                if quote:
                    return EvidenceDetailView(
                        evidenceId=evidence_id,
                        rawText=quote,
                    )
        raise BusinessError("evidence_not_found", "未找到证据", status_code=404)

    def scoring_status(
        self, application_id: str, *, user: User, source: ApplicationReadSource | None = None
    ) -> ScoringStatusView:
        source = source or self.source.load(application_id)
        app = source.application
        run = self.source.latest_workflow_run(application_id, "scoring_workflow")
        if run is not None:
            process = workflow_process_view(self.db, run)
            return ScoringStatusView(
                applicationId=application_id,
                jobId=app.job_id,
                resumeSubmissionId=app.adopted_resume_submission_id,
                workflowRunId=run.workflow_run_id,
                workflowType=run.workflow_type,
                runStatus=process.process_status,
                applicationStatus=app.status,
                stage="screening",
                updatedAt=_iso(run.updated_at or run.completed_at or run.started_at),
                error=None,
                process=process.to_public_dict() if process.workflow_run_id else None,
                recoveryActions=self._recovery_actions(app, user),
            )
        assessment = self._latest_assessment(application_id, source=source) or {}
        score_status = assessment.get("scoreStatus")
        if app.status == "screening_running":
            run_status = "running"
        elif score_status == "failed":
            run_status = "failed"
        elif score_status == "scored" or app.status not in {"submitted", "screening_running"}:
            run_status = "completed"
        else:
            run_status = "not_started"
        error = assessment.get("failureReason") or assessment.get("error")
        return ScoringStatusView(
            applicationId=application_id,
            jobId=app.job_id,
            resumeSubmissionId=app.adopted_resume_submission_id,
            workflowRunId=None,
            workflowType="scoring_workflow",
            runStatus=run_status,
            applicationStatus=app.status,
            stage=str(assessment.get("currentStage") or "screening"),
            updatedAt=_iso(app.updated_at),
            error=None,
            process=None,
            recoveryActions=self._recovery_actions(app, user),
        )

    def workflow_run_status(self, workflow_run_id: str) -> ScoringStatusView:
        run = self.db.get(WorkflowRun, workflow_run_id)
        if run is None:
            raise BusinessError("workflow_run_not_found", "未找到后台任务", status_code=404)
        app = self.db.get(Application, run.application_id)
        if app is None:
            raise BusinessError("application_not_found", "未找到候选申请", status_code=404)
        return ScoringStatusView(
            applicationId=run.application_id,
            jobId=app.job_id,
            workflowRunId=run.workflow_run_id,
            workflowType=run.workflow_type,
            runStatus={
                "pending": "queued",
                "running": "running",
                "completed": "completed",
                "failed": "failed",
            }.get(run.status, "failed"),
            applicationStatus=app.status,
            stage={
                "scoring_workflow": "screening",
                "first_interview_planning_workflow": "interview_1_planning",
                "post_first_scoring_workflow": "interview_1",
                "post_second_scoring_workflow": "interview_2",
            }.get(run.workflow_type, run.workflow_type),
            updatedAt=_iso(run.updated_at or run.completed_at or run.started_at),
            error=run.error_message,
        )
