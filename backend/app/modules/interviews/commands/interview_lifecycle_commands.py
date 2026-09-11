"""Interview 生命周期命令：推进面试阶段并保存面试进度。"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.entities import (
    Application,
    HumanDecision,
    Interview,
    StageHistory,
    Task,
    User,
)
from backend.app.modules.tasks.public import TaskWriteService
from backend.app.shared.audit import record_audit_event
from backend.app.modules.auth.public import assert_permission
from backend.app.shared.errors import BusinessError
from backend.app.modules.applications.public import ApplicationProcessService
from backend.app.modules.interviews.services.first_assessment_readiness_service import FirstAssessmentReadinessService


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12].upper()}"


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _iso(value: datetime | None) -> str:
    return value.isoformat(sep=" ", timespec="seconds") if value else ""


class InterviewLifecycleCommands:
    """Owns short interview-stage writes; no model inference is executed here."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def approve_first_interview(
        self, user: User, app: Application, body: dict[str, Any]
    ) -> dict[str, Any]:
        return self._decision_action(
            user,
            app,
            "approve_first_interview",
            "approve_first_interview",
            "部门主管决定进入技术一面，流程进入一面题单确认阶段。",
            after=lambda: self._open_first_interview(app),
        )

    def start_first_interview(
        self, user: User, app: Application, body: dict[str, Any]
    ) -> dict[str, Any]:
        response = self._simple_action(user, app, "start_first_interview", "技术一面已开始。")
        interview = self.db.get(Interview, f"INT_{app.application_id}_FIRST")
        if interview is not None:
            interview.status = "recording"
        return response

    def save_first_interview_progress(
        self, user: User, app: Application, body: dict[str, Any]
    ) -> dict[str, Any]:
        assert_permission(self.db, user, app, "save_first_interview_progress")
        if app.status not in {"first_interview_in_progress", "first_interview_evaluation"}:
            raise BusinessError(
                "first_interview_progress_state_invalid",
                f"当前状态 {app.status} 不能保存一面过程记录",
                status_code=409,
            )
        draft = self.upsert_first_progress(app, user, body)
        # 提交由上层 CommandRunner 统一负责。
        return {"draft_version": draft["draftVersion"], "updated_at": draft["updatedAt"]}

    def finish_first_interview(
        self, user: User, app: Application, body: dict[str, Any]
    ) -> dict[str, Any]:
        assert_permission(self.db, user, app, "finish_first_interview")
        from_status = app.status
        to_status = self._transition(app, "finish_first_interview")
        self.upsert_first_progress(app, user, body)
        self._stage(app, user, "finish_first_interview", from_status, to_status, "技术一面已结束，进入面后评价确认。")
        ApplicationProcessService.apply_transition(
            app, ApplicationProcessService.plan_transition(app, action="finish_first_interview"),
            now=_now(), owner=self._user_name(app.assigned_first_interviewer),
        )
        return self._response(app, "技术一面已结束，请确认一面面评")

    def approve_second_interview(
        self, user: User, app: Application, body: dict[str, Any]
    ) -> dict[str, Any]:
        # 读模型门禁不能替代写路径门禁：绕过页面直接调用命令同样必须等待 V2 正式发布。
        FirstAssessmentReadinessService(self.db).require_ready_for_second_review(app)
        return self._decision_action(
            user,
            app,
            "approve_second_interview",
            "approve_second_interview",
            "HR 决定进入二面，开始自由记录。",
            after=lambda: self._open_second_interview(app),
        )

    def start_second_interview(
        self, user: User, app: Application, body: dict[str, Any]
    ) -> dict[str, Any]:
        response = self._simple_action(user, app, "start_second_interview", "HR 二面已开始。")
        interview = self.db.get(Interview, f"INT_{app.application_id}_SECOND")
        if interview is not None:
            interview.status = "recording"
        return response

    def save_second_interview_progress(
        self, user: User, app: Application, body: dict[str, Any]
    ) -> dict[str, Any]:
        assert_permission(self.db, user, app, "save_second_interview_progress")
        if app.status not in {"second_interview_in_progress", "second_interview_evaluation"}:
            raise BusinessError(
                "second_interview_progress_state_invalid",
                f"当前状态 {app.status} 不能保存二面过程记录",
                status_code=409,
            )
        draft = self.upsert_second_progress(app, user, body)
        # 提交由上层 CommandRunner 统一负责。
        return {"draft_version": draft["draftVersion"], "updated_at": draft["updatedAt"]}

    def finish_second_interview(
        self, user: User, app: Application, body: dict[str, Any]
    ) -> dict[str, Any]:
        assert_permission(self.db, user, app, "finish_second_interview")
        from_status = app.status
        to_status = self._transition(app, "finish_second_interview")
        self.upsert_second_progress(app, user, body)
        self._stage(app, user, "finish_second_interview", from_status, to_status, "HR 二面已结束，进入面后评价确认。")
        ApplicationProcessService.apply_transition(
            app, ApplicationProcessService.plan_transition(app, action="finish_second_interview"),
            now=_now(), owner="HR 王敏",
        )
        return self._response(app, "HR 二面已结束，请确认二面面评")

    def upsert_first_progress(
        self, app: Application, user: User, body: dict[str, Any]
    ) -> dict[str, Any]:
        return self._upsert_interview_progress(
            app, user, body,
            interview_id=f"INT_{app.application_id}_FIRST",
            default_guide_id=f"PLAN_{app.application_id}_FIRST",
        )

    def upsert_second_progress(
        self, app: Application, user: User, body: dict[str, Any]
    ) -> dict[str, Any]:
        return self._upsert_interview_progress(
            app, user, body,
            interview_id=f"INT_{app.application_id}_SECOND",
            default_guide_id=f"PLAN_{app.application_id}_HR_SECOND",
        )

    def _upsert_interview_progress(
        self,
        app: Application,
        user: User,
        body: dict[str, Any],
        *,
        interview_id: str,
        default_guide_id: str,
    ) -> dict[str, Any]:
        """将过程记录写入唯一正式 Interview，禁止再创建独立草稿对象。"""
        interview = self.db.get(Interview, interview_id)
        if interview is None:
            raise BusinessError("interview_progress_interview_missing", "当前阶段尚未创建正式面试记录", status_code=409)
        now = _now()
        interview.guide_id = str(body.get("guideId") or interview.guide_id or default_guide_id)
        interview.progress_raw_notes = str(body.get("rawNotes") or interview.progress_raw_notes or "")
        if "questionResponses" in body:
            interview.progress_question_responses_json = list(body.get("questionResponses") or [])
        interview.progress_version = int(interview.progress_version or 0) + 1
        interview.progress_updated_by = user.user_id
        interview.progress_created_at = interview.progress_created_at or now
        interview.progress_updated_at = now
        return {
            "interviewId": interview.interview_id,
            "applicationId": app.application_id,
            "guideId": interview.guide_id,
            "rawNotes": interview.progress_raw_notes or "",
            "questionResponses": list(interview.progress_question_responses_json or []),
            "updatedBy": user.user_id,
            "draftVersion": interview.progress_version,
            "updatedAt": _iso(now),
            "createdAt": _iso(interview.progress_created_at),
        }

    def _open_first_interview(self, app: Application) -> None:
        self._complete_task(app.application_id, "department_review")
        self.db.add(
            Interview(
                interview_id=f"INT_{app.application_id}_FIRST",
                application_id=app.application_id,
                interview_type="technical_first_round",
                status="created",
            )
        )
        self._create_task(app, "confirm_first_guide", "确认一面正式题单", app.assigned_first_interviewer, "department_recruiter")

    def _open_second_interview(self, app: Application) -> None:
        self._complete_task(app.application_id, "review_for_second_interview")
        self.db.add(
            Interview(
                interview_id=f"INT_{app.application_id}_SECOND",
                application_id=app.application_id,
                interview_type="hr_second_round",
                status="created",
            )
        )
        self._create_task(app, "conduct_second_interview", "进行 HR 二面并提交自由记录", app.assigned_hr, "hr")

    def _simple_action(self, user: User, app: Application, action: str, note: str) -> dict[str, Any]:
        assert_permission(self.db, user, app, action)
        from_status = app.status
        to_status = self._transition(app, action)
        self._stage(app, user, action, from_status, to_status, note)
        ApplicationProcessService.apply_transition(
            app, ApplicationProcessService.plan_transition(app, action=action),
            now=_now(), owner=user.display_name,
        )
        return self._response(app, "流程动作已完成")

    def _decision_action(
        self,
        user: User,
        app: Application,
        permission_action: str,
        state_action: str,
        reason: str,
        *,
        after,
    ) -> dict[str, Any]:
        assert_permission(self.db, user, app, permission_action)
        effective_at = _now()
        from_status = app.status
        to_status = self._transition(app, state_action)
        self.db.add(
            HumanDecision(
                decision_id=_id("HD"),
                application_id=app.application_id,
                actor_role=user.role,
                actor_name=user.display_name,
                decision=state_action,
                from_status=from_status,
                to_status=to_status,
                reason=reason,
                effective_at=effective_at,
                business_timezone="Asia/Shanghai",
            )
        )
        self._stage(app, user, state_action, from_status, to_status, reason, effective_at=effective_at)
        after()
        ApplicationProcessService.apply_transition(
            app, ApplicationProcessService.plan_transition(app, action=state_action),
            now=effective_at, owner=user.display_name,
        )
        record_audit_event(
            self.db,
            actor=user,
            action="application.decision",
            target_type="application",
            target_id=app.application_id,
            summary=f"人工决策：{state_action}",
            details={"fromStatus": from_status, "toStatus": to_status, "reason": reason},
        )
        return self._response(app, "人工决策已保存")

    def _transition(self, app: Application, action: str) -> str:
        """面试命令只取得迁移计划；正式写入统一由 ApplicationProcessService 完成。"""
        return ApplicationProcessService.plan_transition(app, action=action).to_status

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
    def _user_name(self, user_id: str) -> str:
        user = self.db.get(User, user_id)
        return user.display_name if user else ""

    def _response(self, app: Application, message: str) -> dict[str, Any]:
        self.db.flush()
        return {"application_id": app.application_id, "status": app.status, "message": message}



