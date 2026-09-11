"""一面后 V2、二面后 V3 的用户侧重新计算命令。"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.infrastructure.command_runtime import CommandRunner, CommandSpec
from backend.app.infrastructure.observability.event_contracts import (
    ExecutionEventType,
    ExecutionSeverity,
)
from backend.app.infrastructure.observability.execution_event_writer import (
    WorkflowExecutionEventWriter,
)
from backend.app.infrastructure.workflow_runtime import WorkflowQueue
from backend.app.models.entities import (
    Application,
    ApplicationAssessmentVersion,
    Interview,
    StageHistory,
    User,
    WorkflowRun,
    WorkflowStepCheckpoint,
)
from backend.app.modules.auth.public import ACTION_PERMISSIONS
from backend.app.modules.interviews.commands.interview_record_commands import InterviewRecordCommands
from backend.app.modules.interviews.workflows.post_interview_topology_plan import (
    POST_INTERVIEW_TOPOLOGY_DEFINITION_VERSION,
    topology_step_names,
)
from backend.app.modules.applications.application_recovery import (
    clear_application_recovery_for_prefix,
)
from backend.app.shared.audit import record_audit_event
from backend.app.shared.errors import BusinessError
from backend.app.shared.workflows import StepStatus, WorkflowRunStatus
from backend.app.shared.workflows.status_contracts import (
    ensure_step_transition,
    ensure_workflow_transition,
)


_WORKFLOW_BY_ACTION = {
    "retry_post_first_scoring": "post_first_scoring_workflow",
    "edit_first_interview_feedback": "post_first_scoring_workflow",
    "retry_post_second_scoring": "post_second_scoring_workflow",
    "edit_second_interview_feedback": "post_second_scoring_workflow",
}


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12].upper()}"


class PostInterviewScoringRetryService:
    """重新入队 V2/V3，但绝不再次提交面评或推进招聘主状态。

    一个申请的同阶段评分始终只允许一个 active WorkflowRun。若用户在运行中
    点击重新计算，旧任务会先被标记为 ``cancelled`` 并取消未完成检查点；
    StepRunner 随后失去租约，不能再发布旧结果。
    """

    def __init__(self, db: Session) -> None:
        self.db = db
        self.queue = WorkflowQueue(db)
        self.records = InterviewRecordCommands(db)
        self.execution_events = WorkflowExecutionEventWriter()

    def request(
        self,
        *,
        user: User,
        application_id: str,
        idempotency_key: str,
        action: str,
        feedback: dict | None = None,
    ) -> dict:
        workflow_type = _WORKFLOW_BY_ACTION.get(action)
        if workflow_type is None or action not in ACTION_PERMISSIONS:
            raise RuntimeError(f"post_interview_retry_action_invalid:{action}")
        permission_code = ACTION_PERMISSIONS[action]

        def handler(context) -> dict:
            return self._request_in_transaction(
                app=context.resource,
                user=user,
                action=action,
                workflow_type=workflow_type,
                feedback=feedback,
            )

        return CommandRunner(self.db).execute(
            spec=CommandSpec(
                action=action,
                resource_type="application",
                permission_code=permission_code,
                authorization_action=action,
                idempotency_resource_key="application_id",
            ),
            user=user,
            resource_id=application_id,
            body={"recalculation": True, "feedbackRepair": feedback is not None},
            idempotency_key=idempotency_key,
            handler=handler,
            idempotency_extra={"workflow_type": workflow_type},
        )

    def _request_in_transaction(
        self,
        *,
        app: Application,
        user: User,
        action: str,
        workflow_type: str,
        feedback: dict | None = None,
    ) -> dict:
        original = self._latest_run_for_update(app.application_id, workflow_type)
        if original is None:
            raise BusinessError(
                "post_interview_scoring_not_started",
                "尚未提交本轮面评，不能重新计算评估",
                status_code=409,
            )

        self._ensure_source_repair_ready(app=app, workflow_type=workflow_type)

        replaced_active_run_id: str | None = None
        if original.status in {"pending", "running"}:
            replaced_active_run_id = original.workflow_run_id
            self._cancel_active_run(original)
            # enqueue 会查询活跃任务；必须先把取消状态 flush 到数据库，避免同一事务内
            # 的旧 pending/running 任务仍被队列去重逻辑识别为活跃任务。
            self.db.flush()

        repaired_feedback: dict | None = None
        if feedback is not None:
            repaired_feedback = self._persist_repaired_feedback(
                app=app, user=user, workflow_type=workflow_type, feedback=feedback,
            )

        # 重试必须升级到当前冻结拓扑定义；不得把旧动态重算 v2 作为新运行恢复。
        retry = self.queue.retry(
            original,
            triggered_by=user.user_id,
            reason="user_requested_recalculation",
            definition_version=POST_INTERVIEW_TOPOLOGY_DEFINITION_VERSION,
        )
        source_recovery = self._is_same_stage_source_recovery(app, workflow_type)
        retry_scope = (
            None
            if repaired_feedback is not None
            else self._copy_reusable_checkpoints(
                original,
                retry,
                force_freeze_sources=source_recovery,
            )
        )
        retry.input_json = {
            **dict(retry.input_json or {}),
            **({"body": repaired_feedback} if repaired_feedback is not None else {}),
            "retry_scope": retry_scope or "full_recalculation",
            # 这是一次新的用户重算业务请求。发布步骤用该稳定标识区分“同一
            # Workflow 的幂等重试”和“用户再次主动重算”：前者只能发布一次，
            # 后者即使冻结输入完全相同，也必须形成新的正式评估版本。
            "recalculation_request_id": retry.workflow_run_id,
        }
        self._restore_interview_projection(app, workflow_type)
        clear_application_recovery_for_prefix(
            app,
            "post_first_"
            if workflow_type == "post_first_scoring_workflow"
            else "post_second_",
        )
        now = _now()
        round_label = "一面后" if workflow_type == "post_first_scoring_workflow" else "二面后"
        self.db.add(
            StageHistory(
                stage_history_id=_id("SH"),
                application_id=app.application_id,
                actor_role=user.role,
                actor_name=user.display_name,
                action=action,
                from_status=app.status,
                to_status=app.status,
                note=(
                    f"已替代正在执行的{round_label}评估，开始重新计算。"
                    if replaced_active_run_id
                    else f"已提交{round_label}评估重新计算。"
                ),
                effective_at=now,
                business_timezone="Asia/Shanghai",
            )
        )
        record_audit_event(
            self.db,
            actor=user,
            action="application.assessment.recalculate",
            target_type="application",
            target_id=app.application_id,
            summary=f"重新计算{round_label}评估",
            details={
                "workflowType": workflow_type,
                "workflowRunId": retry.workflow_run_id,
                "retryOf": original.workflow_run_id,
                "replacedActiveRunId": replaced_active_run_id,
            },
            workflow_run_id=retry.workflow_run_id,
        )
        self.db.flush()
        return {
            "application_id": app.application_id,
            "status": app.status,
            "message": f"{round_label}评估已重新排队计算",
            "workflow_run_id": retry.workflow_run_id,
            "run_status": "pending",
        }

    def _persist_repaired_feedback(
        self, *, app: Application, user: User, workflow_type: str, feedback: dict,
    ) -> dict:
        """把用户修正保存为新不可变 InterviewRecord，并仅冻结这批新记录。"""
        payload = dict(feedback or {})
        has_notes = bool(str(payload.get("rawNotes") or "").strip())
        has_responses = bool(payload.get("questionResponses"))
        if not has_notes and not has_responses:
            raise BusinessError(
                "post_interview_feedback_repair_empty",
                "请至少填写一条面评记录后再重新计算",
                status_code=422,
            )
        first = workflow_type == "post_first_scoring_workflow"
        stage = "interview_1" if first else "interview_2"
        suffix = "FIRST" if first else "SECOND"
        self.records.persist(
            app,
            interview_id=f"INT_{app.application_id}_{suffix}",
            stage=stage,
            body=payload,
        )
        # persist 会把本次创建的稳定 Record ID 写回 payload；冻结步骤据此排除旧记录。
        if not payload.get("_sourceInterviewRecordIds"):
            raise BusinessError(
                "post_interview_feedback_repair_invalid",
                "面评记录没有可保存的有效内容，请修改后重试",
                status_code=422,
            )
        return payload

    def _copy_reusable_checkpoints(
        self,
        original: WorkflowRun,
        retry: WorkflowRun,
        *,
        force_freeze_sources: bool = False,
    ) -> str | None:
        """复用失败步骤之前的成功 Artifact；已判坏的来源或工件绝不复制。"""
        if force_freeze_sources:
            # 上游修复后的第一步必须重新读取最新正式 V1/V2。复制旧 freeze
            # 检查点虽然更快，却会让本次重试继续引用修复前的不可变版本。
            return "freeze_post_interview_sources"
        if (
            original.status not in {"failed", "blocked"}
            or original.definition_version != POST_INTERVIEW_TOPOLOGY_DEFINITION_VERSION
        ):
            return None
        step_names = set(topology_step_names())
        failed = self.db.scalar(
            select(WorkflowStepCheckpoint)
            .where(
                WorkflowStepCheckpoint.workflow_run_id == original.workflow_run_id,
                WorkflowStepCheckpoint.status.in_(("failed", "blocked")),
            )
            .order_by(WorkflowStepCheckpoint.step_order.asc())
        )
        if failed is None or failed.step_name not in step_names:
            return None
        restart_step = failed.step_name
        restart_order = failed.step_order
        successful = list(self.db.scalars(
            select(WorkflowStepCheckpoint)
            .where(
                WorkflowStepCheckpoint.workflow_run_id == original.workflow_run_id,
                WorkflowStepCheckpoint.status == "succeeded",
                WorkflowStepCheckpoint.step_order < failed.step_order,
            )
            .order_by(WorkflowStepCheckpoint.step_order.asc())
        ))
        if failed.last_error_code == "post_interview_anchor_updates_missing":
            # 该错误说明解析断言或锚点绑定没有形成可用更新；只重跑纯评分步骤
            # 会继续复用同一份坏产物，因此必须从面评解析开始重新生成。
            restart_step = "parse_interview_units"
            restart_order = topology_step_names().index(restart_step) + 1
        else:
            message = str(failed.last_error_message or "")
            publish_restart = self._publish_contract_restart_step(
                error_code=str(failed.last_error_code or ""),
                error_message=message,
            )
            if failed.step_name == "publish_post_interview_assessment" and publish_restart:
                restart_step = publish_restart
                restart_order = topology_step_names().index(restart_step) + 1
            artifact_failure = any(marker in message for marker in (
                "workflow_artifact_",
                "post_interview_step_artifact_missing",
                "post_interview_source_manifest_missing",
            ))
            if artifact_failure:
                # StepRunner 会把意外 RuntimeError 的稳定码记录为异常类型，因此这里
                # 使用完整错误信息中的 artifactId 反查生产步骤。无法定位时从冻结来源
                # 重跑，不能继续复制已知损坏的检查点。
                producer = next(
                    (
                        checkpoint.step_name
                        for checkpoint in successful
                        if str(
                            (checkpoint.output_refs_json or {}).get("artifactId") or ""
                        )
                        and str(
                            (checkpoint.output_refs_json or {}).get("artifactId") or ""
                        ) in message
                    ),
                    None,
                )
                if producer is None and "post_interview_step_artifact_missing:" in message:
                    candidate = message.rsplit(":", 1)[-1].strip()
                    producer = candidate if candidate in step_names else None
                restart_step = producer or "freeze_post_interview_sources"
                restart_order = topology_step_names().index(restart_step) + 1
        checkpoints = [
            checkpoint
            for checkpoint in successful
            if checkpoint.step_order < restart_order
        ]
        now = _now()
        for checkpoint in checkpoints:
            if checkpoint.step_name not in step_names or not checkpoint.output_refs_json:
                continue
            self.db.add(WorkflowStepCheckpoint(
                checkpoint_id=_id("WSC"),
                workflow_run_id=retry.workflow_run_id,
                step_name=checkpoint.step_name,
                step_order=checkpoint.step_order,
                definition_version=POST_INTERVIEW_TOPOLOGY_DEFINITION_VERSION,
                status="succeeded",
                input_hash=checkpoint.input_hash,
                idempotency_key=f"{retry.workflow_run_id}:{checkpoint.step_name}"[:128],
                external_request_id=f"{retry.workflow_run_id}:{checkpoint.step_name}:artifact-reuse"[:128],
                output_refs_json=dict(checkpoint.output_refs_json or {}),
                attempt_count=checkpoint.attempt_count,
                max_attempts_snapshot=checkpoint.max_attempts_snapshot,
                max_poll_attempts_snapshot=checkpoint.max_poll_attempts_snapshot,
                poll_count=checkpoint.poll_count,
                completed_at=now,
                created_at=now,
                updated_at=now,
            ))
        return restart_step

    @staticmethod
    def _publish_contract_restart_step(
        *, error_code: str, error_message: str,
    ) -> str | None:
        """把发布期的确定性合同错误送回工件生产者；事务错误仍只重发。"""
        detail = f"{error_code} {error_message}".casefold()
        if any(marker in detail for marker in (
            "frozen_profile", "source_manifest", "previous_topology", "topology_",
        )):
            return "freeze_post_interview_sources"
        if any(marker in detail for marker in (
            "parse_artifact", "parsedraft", "interviewparsedraft", "assertions",
        )):
            return "parse_interview_units"
        if any(marker in detail for marker in (
            "core_artifact", "assessmentcoreresult", "core_result",
        )):
            return "run_topology_incremental_scoring"
        if any(marker in detail for marker in (
            "rule_artifact", "assessmentruleresult", "rule_result",
        )):
            return "derive_incremental_rules"
        if any(marker in detail for marker in (
            "presentation_artifact", "assessmentpresentationresult", "presentation_result",
        )):
            return "generate_incremental_presentation"
        return None

    @staticmethod
    def _is_same_stage_source_recovery(
        app: Application, workflow_type: str,
    ) -> bool:
        expected = (
            "post_first_source_review_required"
            if workflow_type == "post_first_scoring_workflow"
            else "post_second_source_review_required"
        )
        return str(app.recovery_code or "") == expected

    def _ensure_source_repair_ready(
        self, *, app: Application, workflow_type: str,
    ) -> None:
        """拒绝会原样复现来源错误的重试，不引入额外恢复状态表。"""
        if not self._is_same_stage_source_recovery(app, workflow_type):
            return
        previous_stage = (
            "screening"
            if workflow_type == "post_first_scoring_workflow"
            else "after_first_interview"
        )
        upstream_workflow = (
            "scoring_workflow"
            if workflow_type == "post_first_scoring_workflow"
            else "post_first_scoring_workflow"
        )
        active_upstream = next(
            (
                run
                for run in self.db.scalars(
                    select(WorkflowRun).where(
                        WorkflowRun.application_id == app.application_id,
                        WorkflowRun.workflow_type == upstream_workflow,
                        WorkflowRun.status.in_(("pending", "running")),
                    )
                )
                if (
                    upstream_workflow != "scoring_workflow"
                    or bool((run.input_json or {}).get("rebuild_published_assessment"))
                )
            ),
            None,
        )
        if active_upstream is not None:
            raise BusinessError(
                "post_interview_source_rebuild_in_progress",
                f"上游{('初步筛选依据' if previous_stage == 'screening' else '一面后评估依据')}正在重新生成，完成后再重新计算",
                status_code=409,
            )

        context = (
            dict(app.recovery_context_json or {})
            if isinstance(app.recovery_context_json, dict) else {}
        )
        error_code = str(context.get("errorCode") or "").casefold()
        requires_new_source = any(marker in error_code for marker in (
            "previous_version", "frozen_resume", "frozen_job", "topology",
        ))
        if not requires_new_source or "sourceAssessmentVersionId" not in context:
            return
        latest = self.db.scalars(
            select(ApplicationAssessmentVersion)
            .where(
                ApplicationAssessmentVersion.application_id == app.application_id,
                ApplicationAssessmentVersion.stage == previous_stage,
            )
            .order_by(
                ApplicationAssessmentVersion.version.desc(),
                ApplicationAssessmentVersion.created_at.desc(),
            )
        ).first()
        frozen_id = str(context.get("sourceAssessmentVersionId") or "")
        latest_id = str(latest.assessment_version_id if latest is not None else "")
        if not latest_id or latest_id == frozen_id:
            raise BusinessError(
                "post_interview_source_rebuild_required",
                f"请先重新生成可用的{('初步筛选依据' if previous_stage == 'screening' else '一面后评估依据')}，再重新计算",
                status_code=409,
            )

    def _latest_run_for_update(
        self, application_id: str, workflow_type: str
    ) -> WorkflowRun | None:
        return self.db.scalar(
            select(WorkflowRun)
            .where(
                WorkflowRun.application_id == application_id,
                WorkflowRun.workflow_type == workflow_type,
            )
            .order_by(WorkflowRun.updated_at.desc(), WorkflowRun.started_at.desc())
            .with_for_update()
        )

    def _cancel_active_run(self, run: WorkflowRun) -> None:
        """取消活跃旧任务；外部请求可自然返回，但 StepRunner 不再拥有发布租约。"""
        now = _now()
        ensure_workflow_transition(run.status, WorkflowRunStatus.CANCELLED)
        run.status = WorkflowRunStatus.CANCELLED.value
        run.completed_at = now
        run.available_at = None
        run.error_message = "已被用户发起的重新计算任务替代"
        run.lease_owner = None
        run.lease_expires_at = None
        run.heartbeat_at = now
        run.updated_at = now
        for checkpoint in self.db.scalars(
            select(WorkflowStepCheckpoint)
            .where(WorkflowStepCheckpoint.workflow_run_id == run.workflow_run_id)
            .with_for_update()
        ):
            if checkpoint.status in {
                StepStatus.PENDING.value,
                StepStatus.RUNNING.value,
                StepStatus.RETRY_WAIT.value,
                StepStatus.ACTIVITY_RETRY_WAIT.value,
                StepStatus.WAITING_EXTERNAL.value,
                StepStatus.BLOCKED.value,
            }:
                ensure_step_transition(checkpoint.status, StepStatus.CANCELLED)
                checkpoint.status = StepStatus.CANCELLED.value
                checkpoint.completed_at = now
                checkpoint.next_attempt_at = None
                checkpoint.lease_owner = None
                checkpoint.lease_expires_at = None
                checkpoint.updated_at = now
        self.execution_events.append(
            self.db,
            run=run,
            event_type=ExecutionEventType.WORKFLOW_CANCELLED,
            severity=ExecutionSeverity.WARNING,
            error_code="superseded_by_recalculation",
        )

    def _restore_interview_projection(
        self, app: Application, workflow_type: str
    ) -> None:
        first = workflow_type == "post_first_scoring_workflow"
        suffix = "FIRST" if first else "SECOND"
        interview = self.db.get(Interview, f"INT_{app.application_id}_{suffix}")
        if interview is not None:
            interview.status = "submitted"
        self.records.mark_task_processing(
            app.application_id,
            "conduct_first_interview" if first else "conduct_second_interview",
            "正在重新计算一面后评估" if first else "正在重新计算二面后评估",
        )
