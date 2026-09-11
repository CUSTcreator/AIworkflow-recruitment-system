"""一面题单规划服务：编排版本生成、草稿编辑和正式确认。"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.entities import (
    Application,
    FirstInterviewPlanVersion,
    InterviewGuide,
    Interview,
    InterviewQuestion,
    Job,
    StageHistory,
    User,
    WorkflowRun,
)
from backend.app.modules.applications.application_recovery import (
    application_recovery_plan,
    clear_application_recovery,
)
from backend.app.modules.applications.public import ApplicationProcessService
from backend.app.modules.auth.public import assert_permission
from backend.app.modules.interview_guides.public import InterviewGuideTemplateService
from backend.app.modules.interviews.services.first_interview_planning_input_service import (
    FirstInterviewPlanningInputService,
)
from backend.app.modules.tasks.public import TaskWriteService
from backend.app.shared.errors import BusinessError


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12].upper()}"


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _iso(value: datetime | None) -> str:
    return value.isoformat(sep=" ", timespec="seconds") if value else ""


class FirstInterviewPlanningService:
    """一面题单确认命令服务。生成链路的唯一编排入口位于 Workflow。"""

    def __init__(self, db: Session) -> None:
        self.db = db

    def save_draft(
        self,
        user: User,
        app: Application,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        assert_permission(self.db, user, app, "confirm_first_guide")
        if app.status != "first_interview_planning":
            raise BusinessError(
                "first_interview_guide_draft_state_invalid",
                "当前阶段不能修改一面题单草稿",
                status_code=409,
            )
        plan = body.get("plan")
        if not isinstance(plan, dict):
            raise BusinessError(
                "first_interview_guide_draft_missing",
                "题单草稿不能为空",
                status_code=422,
            )
        row = self._latest_plan(app.application_id, status="draft")
        if row is None:
            raise BusinessError(
                "first_interview_plan_missing",
                "尚未生成一面题单规划版本",
                status_code=409,
            )
        self._assert_current_plan_identity(plan, row, app)
        presentation = dict(row.presentation_json or {})
        current = dict(presentation.get("draft_guide") or {})
        revision = int(current.get("draftRevision") or 0) + 1
        updated_at = _iso(_now())
        draft = {
            **current,
            **plan,
            "applicationId": app.application_id,
            "planVersionId": row.plan_version_id,
            "guideStatus": "draft",
            "draftRevision": revision,
            "updatedAt": updated_at,
            "updatedBy": user.user_id,
            "confirmed": False,
        }
        presentation["draft_guide"] = draft
        # 前端编辑后的题目仍只存在于本规划版本的展示区块，不会提前创建执行态实体。
        if isinstance(draft.get("questionSuggestions"), list):
            presentation["question_suggestions"] = draft["questionSuggestions"]
        row.presentation_json = presentation
        # 提交由上层 CommandRunner 统一负责，使草稿、幂等记录和审计可原子提交。
        return {
            "draft_version": revision,
            "plan_version_id": row.plan_version_id,
            "updated_at": updated_at,
        }

    def confirm(
        self,
        user: User,
        app: Application,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        assert_permission(self.db, user, app, "confirm_first_guide")
        effective_at = _now()
        from_status = app.status
        transition = ApplicationProcessService.plan_transition(app, action="confirm_first_guide")
        to_status = transition.to_status

        plan_version = self._latest_plan(app.application_id, status="draft")
        if plan_version is None:
            raise BusinessError(
                "first_interview_plan_missing",
                "尚未生成一面题单，请先运行题单生成任务",
                status_code=409,
            )
        presentation = dict(plan_version.presentation_json or {})
        guide = body.get("plan")
        if not isinstance(guide, dict):
            guide = dict(presentation.get("draft_guide") or {})
        if not guide:
            raise BusinessError(
                "first_interview_guide_missing",
                "一面题单规划内容为空",
                status_code=409,
            )
        # 空题单也是有效业务选择：面试官可以确认“只使用通用题”或暂时不设置题目。
        # combine_confirmed 会按岗位配置附加通用题；没有通用题时仍物化为空的正式题单，
        # 后续面试记录流程照常可进入，不能再用“至少一题”阻断投递。
        self._assert_current_plan_identity(guide, plan_version, app)
        job = self.db.get(Job, app.job_id)
        if job is None:
            raise BusinessError(
                "application_job_missing",
                "候选申请缺少岗位信息",
                status_code=409,
            )

        # 1. InterviewTarget 已由初筛 V1 发布器在同一事务创建；题单确认只建立正式题单与逐题执行记录。
        #    guide_id 与 plan_version_id 同时落库和写入 payload，保证 V2 可精确冻结本次确认的题目，
        #    不会在同一申请重做题单后混入历史 Question。
        guide_id = _id("IG")
        confirmed = InterviewGuideTemplateService(self.db).combine_confirmed(guide, job)
        confirmed = {
            **confirmed,
            "applicationId": app.application_id,
            "guideId": guide_id,
            "planVersionId": plan_version.plan_version_id,
            "sourceAssessmentVersionId": plan_version.source_assessment_version_id,
            "round": "first",
            "guideType": "technical_first_round",
            "confirmed": True,
            "confirmedAt": _iso(effective_at),
            "guideStatus": "confirmed",
        }
        questions = [
            {
                **question,
                "confirmed": True,
                "guideId": guide_id,
                "planVersionId": plan_version.plan_version_id,
            }
            for question in confirmed.get("questions", [])
            if isinstance(question, dict)
        ]
        confirmed["questions"] = questions
        self.db.add(
            InterviewGuide(
                application_id=app.application_id,
                guide_id=guide_id,
                plan_version_id=plan_version.plan_version_id,
                content_json=confirmed,
            )
        )
        for question in questions:
            self.db.add(
                InterviewQuestion(
                    application_id=app.application_id,
                    guide_id=guide_id,
                    plan_version_id=plan_version.plan_version_id,
                    question_json=question,
                )
            )

        plan_version.status = "confirmed"
        plan_version.confirmed_at = effective_at
        self._stage(
            app,
            user,
            "confirm_first_guide",
            from_status,
            to_status,
            f"一面面试官已确认正式题单，来源规划版本 {plan_version.plan_version_id}。",
            effective_at=effective_at,
        )
        self._complete_task(app.application_id, "confirm_first_guide")
        self._create_task(
            app,
            "conduct_first_interview",
            "进行技术一面并提交面评",
            app.assigned_first_interviewer,
            "department_recruiter",
        )
        # 题单确认后直接进入“一面记录与面评”页面；Interview 实体也在同一短事务中
        # 切至 recording，和 Application 的 in_progress 状态保持一致。
        self._mark_first_interview_recording(app)
        ApplicationProcessService.apply_transition(
            app, transition, now=_now(), owner=self._user(app.assigned_first_interviewer).display_name,
        )
        clear_application_recovery(app)
        return self._response(app, "一面正式题单已确认，可直接记录面评并作出决定", plan=plan_version)

    def continue_manually(
        self,
        user: User,
        app: Application,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        """题单工作流失败后，以人工题纲创建正式版本并直接进入一面记录阶段。

        这是显式的人工业务决策，不会修改失败 WorkflowRun 的状态；该运行记录仍用于
        查看执行轨迹和技术排障。这里仍发布正式 PlanVersion、Guide 与逐题记录，确保
        一面结束后的 V2 评分能够冻结完整、可追溯的输入。
        """
        assert_permission(self.db, user, app, "continue_first_interview_manually")
        if app.status != "first_interview_planning":
            raise BusinessError(
                "first_interview_manual_continue_state_invalid",
                "当前申请不在一面题单规划阶段，不能转为人工题纲",
                status_code=409,
            )
        failed_run = self.db.scalars(
            select(WorkflowRun)
            .where(
                WorkflowRun.application_id == app.application_id,
                WorkflowRun.workflow_type == "first_interview_planning_workflow",
                WorkflowRun.status.in_(("failed", "blocked")),
            )
            .order_by(WorkflowRun.updated_at.desc(), WorkflowRun.created_at.desc())
        ).first()
        if failed_run is None:
            raise BusinessError(
                "first_interview_manual_continue_requires_failure",
                "仅在题单生成任务明确失败或待确认时才能转为人工题纲",
                status_code=409,
            )
        # 新运行必须由恢复合同授权；没有 recovery_code 只可能是迁移前的历史失败，
        # 此时按通用题单失败兜底，确保交付后旧申请同样不需要技术人员改库。
        effective_recovery_code = (
            str(app.recovery_code or "") or "first_interview_planning_retryable"
        )
        recovery_plan = application_recovery_plan(effective_recovery_code)
        if (
            not effective_recovery_code.startswith("first_interview_")
            or "continue_first_interview_manually" not in recovery_plan.actions
        ):
            raise BusinessError(
                "first_interview_manual_continue_not_available",
                "当前异常不允许以人工题纲继续，请按页面提供的恢复操作处理",
                status_code=409,
            )

        # 1. 人工题纲只锁定后续 V2 必需的 V1 与岗位，不重复自动生成专用的
        # Target 上限、画像完整度或生成配置校验，否则人工出口仍会被原错误卡住。
        source_assessment, template, job = FirstInterviewPlanningInputService(
            self.db
        ).load_manual_source(app)
        effective_at = _now()
        transition = ApplicationProcessService.plan_transition(
            app, action="continue_first_interview_manually"
        )
        previous = self._latest_plan(app.application_id)
        guide = body.get("plan") if isinstance(body.get("plan"), dict) else {}
        if not guide:
            guide = self._manual_guide(app, source_assessment.assessment_version_id)

        # 2. 发布人工兜底 PlanVersion。它和自动生成版本结构相同，只在来源/展示层标记不同。
        plan_version = FirstInterviewPlanVersion(
            plan_version_id=_id("FIP"),
            application_id=app.application_id,
            source_assessment_version_id=source_assessment.assessment_version_id,
            previous_plan_version_id=previous.plan_version_id if previous else None,
            template_version_id=(template or {}).get("templateVersionId"),
            version=(previous.version + 1) if previous else 1,
            status="confirmed",
            source_json={
                "schemaVersion": "first_interview_plan_source_v2",
                "sourceAssessmentVersionId": source_assessment.assessment_version_id,
                "sourceAssessmentVersion": source_assessment.version,
                "templateVersionId": (template or {}).get("templateVersionId"),
                "generationMode": "manual_fallback",
                "sourceWorkflowRunId": failed_run.workflow_run_id,
            },
            core_result_json={
                "schemaVersion": "first_interview_question_proposal_v2",
                "generationMode": "manual_fallback",
                "sourceWorkflowRunId": failed_run.workflow_run_id,
                "questionDraftProposals": [],
            },
            rule_result_json={
                "schemaVersion": "first_interview_question_rule_v2",
                "generationMode": "manual_fallback",
                "reason": "first_interview_planning_workflow_failed",
            },
            presentation_json={
                "schemaVersion": "first_interview_plan_presentation_v2",
                "generation_mode": "manual_fallback",
                "generation_warnings": ["自动题单生成失败，已由面试官选择人工题纲继续。"],
                "draft_guide": dict(guide),
            },
            published_at=effective_at,
            confirmed_at=effective_at,
        )
        self.db.add(plan_version)

        # 3. 与普通“确认题单”一样物化正式 Guide/Question，通用模板题仍会自动并入。
        guide_id = _id("IG")
        confirmed = InterviewGuideTemplateService(self.db).combine_confirmed(guide, job)
        confirmed = {
            **confirmed,
            "applicationId": app.application_id,
            "guideId": guide_id,
            "planVersionId": plan_version.plan_version_id,
            "sourceAssessmentVersionId": source_assessment.assessment_version_id,
            "round": "first",
            "guideType": "technical_first_round",
            "confirmed": True,
            "confirmedAt": _iso(effective_at),
            "guideStatus": "confirmed",
            "generationMode": "manual_fallback",
            "generationWarnings": ["自动题单生成失败，本题纲由面试官人工接管。"],
        }
        questions = [
            {
                **question,
                "confirmed": True,
                "guideId": guide_id,
                "planVersionId": plan_version.plan_version_id,
            }
            for question in confirmed.get("questions", [])
            if isinstance(question, dict)
        ]
        confirmed["questions"] = questions
        self.db.add(
            InterviewGuide(
                application_id=app.application_id,
                guide_id=guide_id,
                plan_version_id=plan_version.plan_version_id,
                content_json=confirmed,
            )
        )
        for question in questions:
            self.db.add(
                InterviewQuestion(
                    application_id=app.application_id,
                    guide_id=guide_id,
                    plan_version_id=plan_version.plan_version_id,
                    question_json=question,
                )
            )

        # 4. 同一命令短事务中写审计、关闭确认任务、创建执行任务并推进申请状态。
        self._stage(
            app,
            user,
            "continue_first_interview_manually",
            app.status,
            transition.to_status,
            f"自动题单任务 {failed_run.workflow_run_id} 未完成，面试官选择人工题纲 V{plan_version.version} 继续。",
            effective_at=effective_at,
        )
        self._complete_task(app.application_id, "confirm_first_guide")
        self._create_task(
            app,
            "conduct_first_interview",
            "进行技术一面并提交面评",
            app.assigned_first_interviewer,
            "department_recruiter",
        )
        self._mark_first_interview_recording(app)
        ApplicationProcessService.apply_transition(
            app,
            transition,
            now=effective_at,
            owner=self._user(app.assigned_first_interviewer).display_name,
        )
        clear_application_recovery(app)
        return self._response(app, "已转为人工题纲，可直接记录面评并作出决定", plan=plan_version)

    def _mark_first_interview_recording(self, app: Application) -> None:
        """同步一面执行实体，避免 Application 已进入执行态但 Interview 仍为 created。"""
        interview = self.db.get(Interview, f"INT_{app.application_id}_FIRST")
        if interview is not None:
            interview.status = "recording"

    @staticmethod
    def _manual_guide(app: Application, source_assessment_version_id: str) -> dict[str, Any]:
        """在页面未提交自定义题目时提供最小正式人工题纲。"""
        return {
            "planId": _id("FIP_MANUAL"),
            "applicationId": app.application_id,
            "sourceAssessmentVersionId": source_assessment_version_id,
            "round": "first",
            "guideType": "technical_first_round",
            "title": "一面人工题纲",
            "summary": "自动题单生成失败，已转由面试官根据岗位要求、简历和初步筛选结论开展一面。",
            "goal": "核验候选人的个人贡献、岗位核心能力与初步筛选待确认项。",
            "durationMinutes": 60,
            "questionCount": 0,
            "targets": [],
            "questions": [],
            "technicalQuestions": [],
            "generationMode": "manual_fallback",
            "generationWarnings": ["自动题单生成失败，需由面试官人工补充技术问题。"],
            "interviewerNotes": "请围绕岗位职责、候选人项目经历与初步筛选待确认项记录面评。",
        }
    def _latest_plan(
        self,
        application_id: str,
        *,
        status: str | None = None,
    ) -> FirstInterviewPlanVersion | None:
        query = select(FirstInterviewPlanVersion).where(
            FirstInterviewPlanVersion.application_id == application_id
        )
        if status is not None:
            query = query.where(FirstInterviewPlanVersion.status == status)
        return self.db.scalars(
            query.order_by(
                FirstInterviewPlanVersion.version.desc(),
                FirstInterviewPlanVersion.created_at.desc(),
            )
        ).first()

    @staticmethod
    def _assert_current_plan_identity(
        plan: dict[str, Any], row: FirstInterviewPlanVersion, app: Application
    ) -> None:
        """阻止旧版本或其他申请的题单覆盖当前草稿。"""
        application_id = str(plan.get("applicationId") or "")
        if application_id and application_id != app.application_id:
            raise BusinessError(
                "first_interview_plan_application_mismatch", "题单不属于当前申请", status_code=422
            )
        plan_version_id = str(plan.get("planVersionId") or "")
        if plan_version_id and plan_version_id != row.plan_version_id:
            raise BusinessError(
                "first_interview_plan_version_outdated", "题单草稿已被新的版本覆盖，请刷新页面", status_code=409
            )
    def _stage(
        self,
        app: Application,
        user: User,
        action: str,
        from_status: str,
        to_status: str,
        note: str,
        *,
        effective_at: datetime | None = None,
    ) -> None:
        self.db.add(
            StageHistory(
                stage_history_id=_id("SH"),
                application_id=app.application_id,
                actor_role=user.role,
                actor_name=user.display_name,
                action=action,
                from_status=from_status,
                to_status=to_status,
                note=note,
                effective_at=effective_at or _now(),
                business_timezone="Asia/Shanghai",
            )
        )

    def _create_task(
        self,
        app: Application,
        task_type: str,
        title: str,
        assignee: str,
        role: str,
    ) -> None:
        TaskWriteService(self.db).ensure_pending(
            application_id=app.application_id,
            task_type=task_type,
            title=title,
            assignee_user_id=assignee,
            assignee_role=role,
        )

    def _complete_task(self, application_id: str, task_type: str) -> None:
        TaskWriteService(self.db).complete(
            application_id=application_id,
            task_type=task_type,
        )

    def _user(self, user_id: str) -> User:
        user = self.db.get(User, user_id)
        if user is None:
            raise BusinessError("user_not_found", "用户不存在", status_code=401)
        return user

    def _response(
        self,
        app: Application,
        message: str,
        *,
        plan: FirstInterviewPlanVersion | None = None,
    ) -> dict[str, Any]:
        self.db.flush()
        return {
            "application_id": app.application_id,
            "status": app.status,
            "message": message,
            "plan_version_id": plan.plan_version_id if plan else None,
            "plan_version": plan.version if plan else None,
        }
