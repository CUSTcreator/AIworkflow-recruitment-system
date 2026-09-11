"""二面查询服务：从正式面试记录、解析结果与评估版本组装二面工作台。"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.entities import (
    ApplicationAssessmentVersion,
    CandidateProfile,
    InterviewParseResultRecord,
    InterviewRecordRecord,
    Interview,
    User,
    WorkflowRun,
)
from backend.app.modules.applications.public import (
    ApplicationReadModelSource,
    allowed_application_actions,
    application_recovery_actions,
    application_recovery_plan,
    application_view,
    job_view,
)
from backend.app.modules.assessment.public import (
    DecisionSummaryQueryService,
    build_candidate_decision_overview,
    build_decision_support,
)
from backend.app.modules.assessment.public import (
    decision_summary as assessment_version_decision_summary,
    decision_summary_view as assessment_version_decision_summary_view,
)
from backend.app.modules.assessment.public import ScreeningQueryService
from backend.app.modules.assessment.queries.assessment_change_view import assessment_change_view
from backend.app.modules.candidates.public import candidate_profile_view
from backend.app.modules.interviews.readModel.non_capability_read_model import build_non_capability_card
from backend.app.shared.errors import BusinessError


class SecondInterviewQueryService:
    """二面、HR 复核和终审页面的聚合查询。

    新链路的正式来源只有 ``InterviewRecordRecord``、``InterviewParseResultRecord``
    和已发布 ``ApplicationAssessmentVersion``。旧的面试笔记、复核包等 payload 表不再参与
    新数据的读取，避免 V2/V3 已发布但页面仍为空的现象。
    """

    def __init__(self, db: Session) -> None:
        self.db = db
        self.source = ApplicationReadModelSource(db)

    def _progress(self, application_id: str) -> dict[str, Any]:
        interview = self.db.get(Interview, f"INT_{application_id}_SECOND")
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

    def _records(self, application_id: str, stage: str) -> list[InterviewRecordRecord]:
        """读取最新评分任务实际冻结的本轮记录；历史修订仍保留但不混入当前展示。"""
        workflow_type = (
            "post_first_scoring_workflow"
            if stage == "interview_1"
            else "post_second_scoring_workflow"
        )
        run = self.db.scalars(
            select(WorkflowRun)
            .where(
                WorkflowRun.application_id == application_id,
                WorkflowRun.workflow_type == workflow_type,
            )
            .order_by(WorkflowRun.updated_at.desc(), WorkflowRun.started_at.desc())
        ).first()
        body = (
            dict((run.input_json or {}).get("body") or {})
            if run is not None and isinstance((run.input_json or {}).get("body"), dict)
            else {}
        )
        has_frozen_ids = "_sourceInterviewRecordIds" in body
        frozen_ids = {
            str(item) for item in body.get("_sourceInterviewRecordIds") or () if str(item)
        }
        if has_frozen_ids and not frozen_ids:
            return []
        query = (
            select(InterviewRecordRecord)
            .where(
                InterviewRecordRecord.application_id == application_id,
                InterviewRecordRecord.stage == stage,
            )
            .order_by(InterviewRecordRecord.created_at.asc(), InterviewRecordRecord.record_id.asc())
        )
        if frozen_ids:
            query = query.where(InterviewRecordRecord.record_id.in_(frozen_ids))
        return list(self.db.scalars(query).all())

    @staticmethod
    def _record_source(row: InterviewRecordRecord) -> dict[str, Any]:
        return dict(row.source_input_json or {})

    def _original_record(self, application_id: str, stage: str, *, include_questions: bool) -> dict[str, Any]:
        """适配正式记录到既有原始记录卡片 DTO，不重新读取旧 QuestionResponse/RawNote。"""
        rows = self._records(application_id, stage)
        free_note = next((row for row in rows if row.record_type == "free_note"), None)
        raw_notes = None if free_note is None else {
            "content": free_note.raw_text,
            "authorName": str(self._record_source(free_note).get("authorName") or ""),
            "createdAt": free_note.created_at.isoformat() if free_note.created_at else "",
        }
        result: dict[str, Any] = {"rawNotes": raw_notes}
        if include_questions:
            recorded_questions: list[dict[str, Any]] = []
            for row in rows:
                if row.record_type != "question_answer":
                    continue
                source = self._record_source(row)
                answer_summary = str(
                    source.get("answerSummary")
                    or source.get("answerText")
                    or source.get("rawText")
                    or ""
                )
                interviewer_note = str(source.get("interviewerNote") or "")
                # 题目只是题单定义；没有回答或备注就不是面试记录，不下发到结果展示层。
                if not answer_summary.strip() and not interviewer_note.strip():
                    continue
                recorded_questions.append(
                    {
                        "questionId": str(row.question_id or row.record_id),
                        "questionText": str(source.get("questionText") or source.get("question") or ""),
                        "answerSummary": answer_summary,
                        "interviewerNote": interviewer_note,
                    }
                )
            result["recordedQuestions"] = recorded_questions
        return result

    def _parse_result(self, application_id: str, stage: str) -> InterviewParseResultRecord | None:
        return self.db.scalars(
            select(InterviewParseResultRecord)
            .where(
                InterviewParseResultRecord.application_id == application_id,
                InterviewParseResultRecord.stage == stage,
            )
            .order_by(InterviewParseResultRecord.version.desc(), InterviewParseResultRecord.created_at.desc())
        ).first()

    def _first_evidence(self, application_id: str) -> list[dict[str, Any]]:
        """把 V2 对应 IPR 的观察结果投影为页面“能力更新”卡片所需证据。"""
        row = self._parse_result(application_id, "after_first_interview")
        if row is None:
            return []
        observations = [
            item for item in (row.assertions_json or [])
            if isinstance(item, dict) and item.get("disposition") == "scored"
        ]
        result: list[dict[str, Any]] = []
        for index, item in enumerate(observations, start=1):
            if not isinstance(item, dict):
                continue
            evidence_ids = [str(item.get("assertionId") or "")]
            result.append({
                "interviewEvidenceId": str(item.get("assertionId") or f"{row.parse_result_id}:{index}"),
                "applicationId": application_id,
                "interviewRound": "first",
                "requirementId": str((item.get("candidateAnchorIds") or [""])[0]),
                "polarity": "neutral",
                "text": str(item.get("text") or ""),
                "sourceEvidenceIds": evidence_ids,
            })
        return result

    def review(self, application_id: str, user: User) -> dict[str, Any]:
        result = self.workspace(application_id, user, non_capability_stage="after_first_interview")
        result["viewSchemaVersion"] = "second_interview_review_v2"
        return result

    def final_review(self, application_id: str, user: User) -> dict[str, Any]:
        result = self.workspace(application_id, user, non_capability_stage="after_second_interview")
        result["viewSchemaVersion"] = "final_review_v2"
        return result

    def workspace(
        self,
        application_id: str,
        user: User,
        *,
        non_capability_stage: str = "after_first_interview",
    ) -> dict[str, Any]:
        source = self.source.load(application_id)
        app = source.application
        candidate = source.candidate
        job = source.job
        screening_query = ScreeningQueryService(self.db)
        screening = screening_query.screening_summary(application_id, source=source)
        if not candidate or not job:
            raise BusinessError("second_interview_workspace_incomplete", "二面工作区缺少候选人或岗位数据", status_code=409)

        screening_snapshot = self.source.score_snapshot_view(source, "screening")
        after_first_snapshot = self.source.score_snapshot_view(source, "after_first_interview")
        after_second_snapshot = self.source.score_snapshot_view(source, "after_second_interview")
        progress = self._progress(application_id)
        # 二面没有题单规划；此字段保留为空，只为兼容既有 DTO。
        plan = None
        first_evidence = self._first_evidence(application_id)
        first_assessment = source.assessment_versions.get("after_first_interview")
        second_assessment = source.assessment_versions.get("after_second_interview")
        decision_summary = (
            assessment_version_decision_summary(source.current_assessment_version)
            if source.current_assessment_version is not None
            else DecisionSummaryQueryService(self.db).latest(application_id, source=source)
        )
        decision_support = build_decision_support(
            screening,
            plan=plan,
            progress=progress,
            first_assessment=(first_assessment.core_result_json if first_assessment else None),
            review_package=None,
            final_package=(second_assessment.presentation_json if second_assessment else None),
            decision_summary=decision_summary,
        )
        current_snapshot = after_second_snapshot or after_first_snapshot or screening_snapshot or {}
        action_candidates = {
            "hr_second_review": ["approve_second_interview", "hold", "manual_review", "reject"],
            "second_interview_in_progress": ["save_second_interview_progress", "complete_second_interview"],
            "second_interview_evaluation": ["save_second_interview_progress", "complete_second_interview"],
            # V3 尚在处理或失败时，终审页仍可展示 V2 供用户了解上下文，但不能
            # 让页面暴露最终决定。命令端也会重复校验该正式版本不变量。
            "final_review": ["offer", "manual_review", "reject"] if second_assessment else [],
        }.get(app.status, [])
        screening_result = screening.get("screeningResultView") or {}
        # 二面“不通过”会把 Application 主状态置为 closed_rejected，但 V3 仍可能
        # 正在运行或等待重试。只看主状态会把该任务误判成 V2，终审页也就拿不到
        # V3 的失败原因和重试入口；优先检查是否存在二面评分运行，再回退到 V2。
        post_second_run = self.source.latest_workflow_run(
            application_id, "post_second_scoring_workflow"
        )
        post_first_run = self.source.latest_workflow_run(
            application_id, "post_first_scoring_workflow"
        )
        if app.status in {"second_interview_evaluation", "final_review"} or post_second_run is not None:
            workflow_type = "post_second_scoring_workflow"
            workflow_run = post_second_run
        else:
            workflow_type = "post_first_scoring_workflow"
            workflow_run = post_first_run
        # V2/V3 的“重新计算”不改变 Application 主状态。正常/完成状态允许主动重算；
        # 异常状态则必须服从后端 recoveryActions，不能额外暴露会复现确定性错误的按钮。
        retry_action = (
            "retry_post_second_scoring"
            if workflow_type == "post_second_scoring_workflow"
            else "retry_post_first_scoring"
        )
        recovery_plan = (
            application_recovery_plan(app.recovery_code)
            if str(app.recovery_code or "").startswith("post_")
            else None
        )
        if recovery_plan is None and workflow_run is not None and str(workflow_run.status or "") == "blocked":
            # 旧任务或异常中断可能只留下 Workflow blocked 状态，没有写入
            # Application.recovery_code。这里补出同一阶段的用户恢复合同，
            # 保证 V2/V3 工作台始终能查看原因并修改面评后继续。
            recovery_plan = application_recovery_plan(
                "post_first_scoring_retryable"
                if workflow_type == "post_first_scoring_workflow"
                else "post_second_scoring_retryable"
            )
        recovery_actions = application_recovery_actions(
            self.db,
            user,
            app,
            list(recovery_plan.actions) if recovery_plan else [],
            application_recovery_code=str(app.recovery_code or ""),
        )
        if workflow_run is not None and (
            recovery_plan is None
            or retry_action in {
                str(item.get("action") or "") for item in recovery_actions
            }
        ):
            action_candidates.append(retry_action)
        return {
            "application": application_view(self.db, app),
            "candidate": candidate_profile_view(candidate, self.db.get(CandidateProfile, candidate.candidate_id)),
            "job": job_view(job, self.db),
            "hardScreening": screening_query.hard_screening_review(
                application_id, source=source
            ),
            "screening": screening,
            "plan": plan,
            "progressDraft": progress,
            # 下列旧字段固定为空：新链路的正式结论均在 AAV/IPR 中，不能再从旧包表回填。
            "reviewPackage": None,
            "finalPackage": None,
            "hrAssessment": None,
            "firstAssessment": None,
            "firstEvidence": first_evidence,
            "firstOriginalRecord": self._original_record(application_id, "interview_1", include_questions=True),
            "secondOriginalRecord": self._original_record(application_id, "interview_2", include_questions=False),
            "screeningScoreSnapshot": screening_snapshot,
            "afterFirstScoreSnapshot": after_first_snapshot,
            "afterSecondScoreSnapshot": after_second_snapshot,
            # V2/V3 的变化只来自已发布 AAV；审核页不能重新计算或自行比较原始 JSON。
            "afterFirstAssessmentChanges": assessment_change_view(first_assessment),
            "afterSecondAssessmentChanges": assessment_change_view(second_assessment),
            "currentScoreSnapshot": after_second_snapshot or after_first_snapshot or screening_snapshot,
            "decisionSummary": assessment_version_decision_summary_view(source.current_assessment_version) if source.current_assessment_version is not None else None,
            "decisionOverview": build_candidate_decision_overview(
                screening_result=screening_result,
                decision_summary=decision_summary,
                decision_support=decision_support,
                score_snapshot=current_snapshot,
            ),
            "decisionSupport": decision_support,
            # 非能力信息属于当前审核轮次；不能把一面和二面断言混成一张卡片。
            "nonCapabilityCard": build_non_capability_card(
                self.db, application_id, stage=non_capability_stage
            ),
            "evidenceIndex": screening_result.get("evidenceIndex") or {},
            "availableActions": allowed_application_actions(self.db, user, app, action_candidates),
            "recoveryActions": recovery_actions,
            "workflowStatus": {
                "workflowRunId": workflow_run.workflow_run_id if workflow_run else None,
                "workflowType": workflow_type,
                "runStatus": {"pending": "queued", "running": "running", "blocked": "review_required", "completed": "completed", "failed": "failed", "cancelled": "cancelled"}.get(workflow_run.status, "not_started") if workflow_run else "not_started",
                "applicationStatus": app.status,
                "error": workflow_run.error_message if workflow_run else None,
                "recoveryCode": str(app.recovery_code or "") or None,
                "recoveryMessage": recovery_plan.public_message if recovery_plan else None,
                "recoveryContext": dict(app.recovery_context_json or {}),
            },
        }
