"""面试工作流请求命令：在统一命令边界中创建异步任务。"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.infrastructure.command_runtime import CommandRunner, CommandSpec
from backend.app.infrastructure.workflow_runtime import WorkflowQueue
from backend.app.models.entities import (
    Application,
    HumanDecision,
    Interview,
    StageHistory,
    User,
    WorkflowRun,
    WorkflowStepCheckpoint,
)
from backend.app.modules.applications.application_recovery import clear_application_recovery
from backend.app.modules.applications.public import ApplicationProcessService
from backend.app.modules.applications.public import InvalidTransition, next_status
from backend.app.modules.auth.public import ACTION_PERMISSIONS
from backend.app.modules.interviews.commands.interview_record_commands import InterviewRecordCommands
from backend.app.modules.interviews.workflows.post_interview_topology_plan import (
    POST_INTERVIEW_TOPOLOGY_DEFINITION_VERSION,
)
from backend.app.modules.tasks.public import TaskWriteService
from backend.app.shared.audit import record_audit_event
from backend.app.shared.errors import BusinessError

# 工作流定义版本属于持久化运行合同：新提交的 V2/V3 必须进入冻结拓扑 v3 编排。
_WORKFLOW_DEFINITION_VERSION = {
    "post_first_scoring_workflow": POST_INTERVIEW_TOPOLOGY_DEFINITION_VERSION,
    "post_second_scoring_workflow": POST_INTERVIEW_TOPOLOGY_DEFINITION_VERSION,
}


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12].upper()}"





class InterviewWorkflowRequestCommands:
    """Validates and queues interview planning or post-interview scoring."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.records = InterviewRecordCommands(db)
        self.tasks = TaskWriteService(db)
        self.queue = WorkflowQueue(db)

    def accept(
        self,
        *,
        user: User,
        application_id: str,
        idempotency_key: str,
        action: str,
        workflow_type: str,
        body: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], bool]:
        """在同一命令事务中校验、写入面评原文并入队。

        工作流本身仍由 Worker 异步执行；本方法只保证“面评记录、任务状态、
        WorkflowRun、审计事件、幂等响应”要么一并成功，要么一并回滚。
        """
        payload = dict(body or {})
        queued_new = False

        def handler(context) -> dict[str, Any]:
            nonlocal queued_new
            response, queued_new = self.enqueue_in_transaction(
                app=context.resource,
                user=user,
                action=action,
                workflow_type=workflow_type,
                body=payload,
            )
            return response
        if action not in ACTION_PERMISSIONS:
            raise RuntimeError(f"interview_workflow_action_not_defined:{action}")
        permission_code = ACTION_PERMISSIONS[action]
        response = CommandRunner(self.db).execute(
            spec=CommandSpec(
                action=action,
                resource_type="application",
                permission_code=permission_code,
                idempotency_resource_key="application_id",
            ),
            user=user,
            resource_id=application_id,
            body=payload,
            handler=handler,
            idempotency_key=idempotency_key,
            # 保持旧接口的幂等指纹：同一个 key 不能跨 workflow_type 使用。
            idempotency_extra={"workflow_type": workflow_type},
        )
        return response, queued_new
    def enqueue_in_transaction(
        self,
        *,
        app: Application,
        user: User,
        action: str,
        workflow_type: str,
        body: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], bool]:
        """内部入队：只写当前 Session，不自行授权、幂等或 commit。

        用于“批准一面并创建题单规划任务”这类复合命令，使 Application 状态、
        面评原文/任务、WorkflowRun、审计与幂等记录由外层 CommandRunner 一次提交。
        """
        payload = dict(body or {})
        self._validate_action(app, action, payload)
        active = self.queue.active_run(
            workflow_type=workflow_type,
            subject_type="application",
            subject_id=app.application_id,
        )
        if active is not None:
            response = self._response(app, "同类后台任务正在执行")
            response.update({
                "workflow_run_id": active.workflow_run_id,
                "workflow_type": active.workflow_type,
                "run_status": active.status,
            })
            return response, False

        # 一面/二面决定属于招聘主流程：在受理命令的短事务内立即推进 Application。
        # V2/V3 Workflow 只发布评估版本，绝不能再延迟改变或回滚这个主状态。
        self._apply_submission_transition(app=app, user=user, action=action, body=payload)

        if workflow_type == "post_first_scoring_workflow":
            self.records.persist(
                app,
                interview_id=f"INT_{app.application_id}_FIRST",
                stage="interview_1",
                body=payload,
            )
            self._mark_interview_submitted(app, "FIRST")
            # 面评已由面试官正式提交，“进行一面”的人工待办至此结束。V2 的
            # 后台评分只通过 WorkflowRun/执行轨迹呈现，不能作为待办中心的任务。
            self.tasks.complete(
                application_id=app.application_id,
                task_type="conduct_first_interview",
            )
        elif workflow_type == "post_second_scoring_workflow":
            self.records.persist(
                app,
                interview_id=f"INT_{app.application_id}_SECOND",
                stage="interview_2",
                body=payload,
            )
            self._mark_interview_submitted(app, "SECOND")
            # 同 V2：二面面评提交即完成“进行二面”待办；V3 未发布前不产生
            # 任何新的人工待办，避免把后台计算误显示为用户待处理事项。
            self.tasks.complete(
                application_id=app.application_id,
                task_type="conduct_second_interview",
            )

        run = (
            self._enqueue_first_planning_publish_only_retry(app=app)
            if workflow_type == "first_interview_planning_workflow"
            and app.recovery_code == "first_interview_publish_retryable"
            else None
        )
        if run is None:
            run, _ = self.queue.enqueue(
                workflow_type=workflow_type,
                subject_type="application",
                subject_id=app.application_id,
                application_id=app.application_id,
                triggered_by=user.user_id,
                input_json={"action": action, "body": payload},
                definition_version=_WORKFLOW_DEFINITION_VERSION.get(workflow_type, 1),
                reuse_active=False,
            )
        if workflow_type in {
            "first_interview_planning_workflow",
            "post_first_scoring_workflow",
            "post_second_scoring_workflow",
        }:
            # 受理成功后旧恢复动作立即失效；后续终态失败会写入本轮的新恢复事实。
            clear_application_recovery(app)
        message = {
            "post_first_scoring_workflow": "一面面评已提交，系统正在更新候选人评估",
            "post_second_scoring_workflow": "二面面评已提交，系统正在更新候选人评估",
        }.get(workflow_type, "后台任务已受理")
        response = self._response(app, message)
        response.update({
            "workflow_run_id": run.workflow_run_id,
            "workflow_type": workflow_type,
            "run_status": "pending",
        })
        record_audit_event(
            self.db,
            actor=user,
            action="application.workflow.requested",
            target_type="application",
            target_id=app.application_id,
            summary=f"人工提交后台流程：{action}",
            details={"workflowType": workflow_type, "workflowRunId": run.workflow_run_id, "action": action},
            workflow_run_id=run.workflow_run_id,
        )
        return response, True

    def _enqueue_first_planning_publish_only_retry(
        self, *, app: Application,
    ) -> WorkflowRun | None:
        """仅发布失败时复用前五步 Artifact，避免再次调用 LLM。"""

        original = self.db.scalar(
            select(WorkflowRun)
            .where(
                WorkflowRun.application_id == app.application_id,
                WorkflowRun.workflow_type == "first_interview_planning_workflow",
                WorkflowRun.status == "failed",
            )
            .order_by(WorkflowRun.updated_at.desc(), WorkflowRun.workflow_run_id.desc())
        )
        if original is None:
            return None
        failed = self.db.scalar(
            select(WorkflowStepCheckpoint)
            .where(
                WorkflowStepCheckpoint.workflow_run_id == original.workflow_run_id,
                WorkflowStepCheckpoint.status == "failed",
            )
            .order_by(WorkflowStepCheckpoint.step_order.desc())
        )
        if failed is None or failed.step_name != "publish_first_interview_plan":
            return None
        retry = self.queue.retry(
            original,
            # Step 输入哈希包含原执行身份，沿用原 actor 才能可靠复用检查点。
            triggered_by=original.triggered_by,
            reason="retry_publish_first_interview_plan",
            definition_version=1,
        )
        retry.input_json = {
            **dict(retry.input_json or {}),
            "retry_scope": "publish_first_interview_plan",
        }
        checkpoints = list(
            self.db.scalars(
                select(WorkflowStepCheckpoint)
                .where(
                    WorkflowStepCheckpoint.workflow_run_id == original.workflow_run_id,
                    WorkflowStepCheckpoint.status == "succeeded",
                    WorkflowStepCheckpoint.step_order < failed.step_order,
                )
                .order_by(WorkflowStepCheckpoint.step_order)
            )
        )
        for checkpoint in checkpoints:
            now = _now()
            self.db.add(WorkflowStepCheckpoint(
                checkpoint_id=_id("WSC"),
                workflow_run_id=retry.workflow_run_id,
                step_name=checkpoint.step_name,
                step_order=checkpoint.step_order,
                definition_version=checkpoint.definition_version,
                status="succeeded",
                input_hash=checkpoint.input_hash,
                idempotency_key=f"{retry.workflow_run_id}:{checkpoint.step_name}"[:128],
                external_request_id=f"{retry.workflow_run_id}:{checkpoint.step_name}:publish-retry"[:128],
                output_refs_json=dict(checkpoint.output_refs_json or {}),
                attempt_count=checkpoint.attempt_count,
                max_attempts_snapshot=checkpoint.max_attempts_snapshot,
                max_poll_attempts_snapshot=checkpoint.max_poll_attempts_snapshot,
                poll_count=checkpoint.poll_count,
                completed_at=now,
                created_at=now,
                updated_at=now,
            ))
        return retry

    def _validate_action(self, app: Application, action: str, body: dict[str, Any]) -> None:
        if action == "run_first_interview_planning":
            if app.status != "first_interview_planning":
                raise BusinessError(
                    "first_interview_planning_state_invalid",
                    f"当前状态 {app.status} 不能生成一面题单",
                    status_code=409,
                )
            return
        transition_action = action
        if action == "complete_first_interview":
            decision = body.get("decision")
            if decision not in {"pass", "reject"}:
                raise BusinessError("first_interview_decision_invalid", "一面决定必须为 pass 或 reject")
            transition_action = f"complete_first_interview_{decision}"
        elif action == "complete_second_interview":
            decision = body.get("decision")
            if decision not in {"pass", "reject"}:
                raise BusinessError("second_interview_decision_invalid", "二面决定必须为 pass 或 reject")
            progress = self._latest_second_progress(app.application_id)
            self._validate_second_feedback({**progress, **body})
            transition_action = f"complete_second_interview_{decision}"
        try:
            next_status(app.status, transition_action)
        except InvalidTransition as exc:
            raise BusinessError("interview_transition_invalid", str(exc), status_code=409) from exc

    def _apply_submission_transition(self, *, app: Application, user: User, action: str, body: dict[str, Any]) -> None:
        """把用户的面试决定立即写入 Application；后台评分失败不影响该决定。"""
        transition_action = action
        if action == "complete_first_interview":
            transition_action = f"complete_first_interview_{body['decision']}"
        elif action == "complete_second_interview":
            transition_action = f"complete_second_interview_{body['decision']}"
        if action not in {
            "complete_first_interview", "complete_second_interview",
            "submit_first_feedback", "submit_second_feedback",
        }:
            return
        transition = ApplicationProcessService.plan_transition(app, action=transition_action)
        now = _now()
        note = {
            "complete_first_interview_pass": "一面通过，系统正在生成一面后评估。",
            "complete_first_interview_reject": "一面不通过，系统正在生成一面后评估。",
            "complete_second_interview_pass": "二面通过，系统正在生成二面后评估。",
            "complete_second_interview_reject": "二面不通过，系统正在生成二面后评估。",
        }.get(transition_action, "面评已提交，系统正在生成后续评估。")
        if action.startswith("complete_"):
            self.db.add(HumanDecision(
                decision_id=_id("HD"), application_id=app.application_id,
                actor_role=user.role, actor_name=user.display_name, decision=transition_action,
                from_status=transition.from_status, to_status=transition.to_status,
                reason=note, effective_at=now, business_timezone="Asia/Shanghai",
            ))
        self.db.add(StageHistory(
            stage_history_id=_id("SH"), application_id=app.application_id,
            actor_role=user.role, actor_name=user.display_name, action=transition_action,
            from_status=transition.from_status, to_status=transition.to_status,
            note=note, effective_at=now, business_timezone="Asia/Shanghai",
        ))
        ApplicationProcessService.apply_transition(app, transition, now=now, owner=user.display_name)

    def _latest_second_progress(self, application_id: str) -> dict[str, Any]:
        """读取正式二面 Interview 的过程字段，不再查询已删除的草稿表。"""
        interview = self.db.get(Interview, f"INT_{application_id}_SECOND")
        if interview is None:
            return {}
        return {
            "interviewId": interview.interview_id,
            "guideId": interview.guide_id,
            "rawNotes": interview.progress_raw_notes or "",
            "questionResponses": list(interview.progress_question_responses_json or []),
            "draftVersion": int(interview.progress_version or 0),
        }

    @staticmethod
    def _validate_second_feedback(body: dict[str, Any]) -> None:
        has_text = isinstance(body.get("rawNotes"), str) and bool(body["rawNotes"].strip())
        has_items = isinstance(body.get("questionResponses"), list) and bool(body["questionResponses"])
        if not has_text and not has_items:
            raise BusinessError(
                "second_interview_feedback_empty",
                "请至少填写二面自由记录或逐题记录",
                status_code=422,
            )

    def _mark_interview_submitted(self, app: Application, suffix: str) -> None:
        interview = self.db.get(Interview, f"INT_{app.application_id}_{suffix}")
        if interview is not None:
            interview.status = "submitted"

    def _response(self, app: Application, message: str) -> dict[str, Any]:
        self.db.flush()
        return {"application_id": app.application_id, "status": app.status, "message": message}
