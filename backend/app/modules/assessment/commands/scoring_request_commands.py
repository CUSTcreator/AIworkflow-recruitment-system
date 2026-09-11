"""初筛评分请求：在统一命令事务中更新状态并创建评分工作流。"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.infrastructure.command_runtime import CommandRunner, CommandSpec
from backend.app.infrastructure.workflow_runtime import WorkflowQueue
from backend.app.infrastructure.workflow_runtime.artifact_store import WorkflowArtifactStore
from backend.app.models.entities import (
    Application,
    JobRequirementProfileRecord,
    JobVersionRecord,
    ResumeSubmission,
    StageHistory,
    User,
    WorkflowRun,
    WorkflowStepCheckpoint,
)
from backend.app.modules.applications.public import ApplicationProcessService
from backend.app.modules.applications.application_recovery import clear_application_recovery
from backend.app.modules.candidates.public import CandidateResumeRebuildService
from backend.app.modules.auth.public import ACTION_PERMISSIONS
from backend.app.modules.jobs.profile_readiness import evaluate_job_profile_record
from backend.app.shared.audit import record_audit_event
from backend.app.shared.errors import BusinessError

def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12].upper()}"


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)




@dataclass(frozen=True)
class ScoringQueueResult:
    """内部入队的持久化结果；调用方仍处于同一数据库事务。"""

    workflow_run: WorkflowRun
    created_or_reused: bool
    message: str


class ScoringRequestCommands:
    """接收初筛请求，并将状态、审计与任务队列原子发布。"""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.queue = WorkflowQueue(db)

    def accept_scoring_request(
        self,
        *,
        user: User,
        application_id: str,
        idempotency_key: str,
        body: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], bool]:
        """HTTP 公开命令：由 CommandRunner 负责授权、幂等和唯一 commit。"""
        payload = dict(body or {})
        queued_new = False

        def handler(context) -> dict[str, Any]:
            nonlocal queued_new
            result = self.enqueue_in_transaction(
                app=context.resource,
                user=user,
                input_json=payload,
                source="manual_scoring_request",
                allow_resume_restructuring=True,
            )
            queued_new = result.created_or_reused
            response = self._response(context.resource, result.message)
            response.update({
                "workflow_run_id": result.workflow_run.workflow_run_id,
                "run_status": result.workflow_run.status,
            })
            return response

        response = CommandRunner(self.db).execute(
            spec=CommandSpec(
                action="run_scoring",
                resource_type="application",
                permission_code=ACTION_PERMISSIONS["run_scoring"],
                idempotency_resource_key="application_id",
            ),
            user=user,
            resource_id=application_id,
            body=payload,
            handler=handler,
            idempotency_key=idempotency_key,
        )
        return response, queued_new

    def accept_screening_rebuild(
        self,
        *,
        user: User,
        application_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Rebuild V1 for a downstream source failure without rewinding Application.

        V1/V2/V3 are immutable assessment versions. A repair creates a new V1 and
        topology definition, while the recruiting stage remains at the current
        review step. After publication, the user explicitly retries the blocked V2.
        """

        def handler(context) -> dict[str, Any]:
            return self.enqueue_rebuild_in_transaction(
                app=context.resource,
                user=user,
            )

        return CommandRunner(self.db).execute(
            spec=CommandSpec(
                action="rebuild_screening_assessment",
                resource_type="application",
                permission_code=ACTION_PERMISSIONS["rebuild_screening_assessment"],
                idempotency_resource_key="application_id",
            ),
            user=user,
            resource_id=application_id,
            body={"sourceRepair": "screening_assessment"},
            idempotency_key=idempotency_key,
            handler=handler,
        )

    def enqueue_rebuild_in_transaction(
        self,
        *,
        app: Application,
        user: User,
    ) -> dict[str, Any]:
        """Create or reuse a V1 rebuild run, preserving the downstream stage."""

        if app.status not in {"hr_second_review", "final_review"}:
            raise BusinessError(
                "screening_rebuild_stage_invalid",
                "只有一面后或二面后评估的来源修复状态可以重新生成初步筛选依据。",
                status_code=409,
            )
        run, created = self.queue.enqueue(
            workflow_type="scoring_workflow",
            subject_type="application",
            subject_id=app.application_id,
            application_id=app.application_id,
            triggered_by=user.user_id,
            input_json={
                "source": "user_requested_screening_rebuild",
                "rebuild_published_assessment": True,
                "rebuild_origin_status": app.status,
            },
            definition_version=2,
            reuse_active=True,
        )
        if created:
            now = _now()
            self.db.add(StageHistory(
                stage_history_id=_id("SH"),
                application_id=app.application_id,
                actor_role=user.role,
                actor_name=user.display_name,
                action="rebuild_screening_assessment",
                from_status=app.status,
                to_status=app.status,
                note="用户请求重新生成初步筛选依据；当前招聘阶段保持不变。",
                effective_at=now,
                business_timezone="Asia/Shanghai",
            ))
            record_audit_event(
                self.db,
                actor=user,
                action="application.assessment.rebuild_screening",
                target_type="application",
                target_id=app.application_id,
                summary="重新生成初步筛选依据",
                details={"workflowRunId": run.workflow_run_id, "preservedStatus": app.status},
                workflow_run_id=run.workflow_run_id,
            )
        return {
            "application_id": app.application_id,
            "status": app.status,
            "message": "初步筛选依据已重新排队生成" if created else "初步筛选依据正在重新生成",
            "workflow_run_id": run.workflow_run_id,
            "run_status": run.status,
        }

    def enqueue_in_transaction(
        self,
        *,
        app: Application,
        user: User,
        input_json: dict[str, Any] | None = None,
        source: str,
        allow_resume_restructuring: bool = True,
    ) -> "ScoringQueueResult":
        """内部编排入口：只写当前 Session，绝不授权、幂等或 commit。

        Candidate 路由、硬筛发布、简历重建及 Workflow Step 必须调用本方法，
        从而把 Application 状态、StageHistory、AuditEvent 与 WorkflowRun 放在调用方
        已打开的同一短事务中。只有 HTTP 路由才可以调用 ``accept_scoring_request``。
        """
        from_status = str(app.status or "")
        if from_status not in {"submitted", "screening_failed"}:
            raise BusinessError(
                "screening_already_started",
                "初步筛选已经启动或完成，请勿重复提交",
                status_code=409,
            )
        if from_status == "screening_failed":
            self._rebind_repaired_job_profile(app)
        if from_status == "screening_failed" and allow_resume_restructuring:
            restructuring_run = self._enqueue_required_resume_restructuring(app, user)
            if restructuring_run is not None:
                record_audit_event(
                    self.db,
                    actor=user,
                    action="application.scoring.requested",
                    target_type="application",
                    target_id=app.application_id,
                    summary="请求重新处理简历并重新进行初步筛选",
                    details={
                        "workflowType": restructuring_run.workflow_type,
                        "workflowRunId": restructuring_run.workflow_run_id,
                        "reason": "upgrade_resume_structure_v2",
                        "source": source,
                    },
                    workflow_run_id=restructuring_run.workflow_run_id,
                )
                return ScoringQueueResult(
                    workflow_run=restructuring_run,
                    created_or_reused=True,
                    message="旧版简历结构正在升级，完成后将自动重新进行初步筛选",
                )

        action = "retry_scoring" if from_status == "screening_failed" else "run_scoring"
        now = _now()
        transition = ApplicationProcessService.plan_transition(app, action=action)
        self.db.add(StageHistory(
            stage_history_id=_id("SH"),
            application_id=app.application_id,
            actor_role=user.role,
            actor_name=user.display_name,
            action=action,
            from_status=transition.from_status,
            to_status=transition.to_status,
            note="初步筛选重试任务已受理。" if action == "retry_scoring" else "初步筛选评分后台任务已受理。",
            effective_at=now,
            business_timezone="Asia/Shanghai",
        ))
        ApplicationProcessService.apply_transition(app, transition, now=now, owner="系统")
        # 新任务已被原子受理后，旧失败的恢复事实不再有效；若本轮再次失败，
        # 对应 Transition 会在同一 Step 终态事务写入新的恢复码。
        clear_application_recovery(app)
        run = (
            self._enqueue_publish_only_retry(app=app)
            if action == "retry_scoring"
            else None
        )
        if run is None:
            run, _ = self.queue.enqueue(
                workflow_type="scoring_workflow",
                subject_type="application",
                subject_id=app.application_id,
                application_id=app.application_id,
                triggered_by=user.user_id,
                input_json={**dict(input_json or {}), "source": source},
                definition_version=2,
                reuse_active=False,
            )
        record_audit_event(
            self.db,
            actor=user,
            action="application.scoring.requested",
            target_type="application",
            target_id=app.application_id,
            summary=("人工重新发起初步筛选评分" if action == "retry_scoring" else "发起初步筛选评分"),
            details={
                "workflowType": run.workflow_type,
                "workflowRunId": run.workflow_run_id,
                "fromStatus": from_status,
                "source": source,
            },
            workflow_run_id=run.workflow_run_id,
        )
        return ScoringQueueResult(
            workflow_run=run,
            created_or_reused=True,
            message=(
                "评分工件已复用，正在重新发布初步筛选结果"
                if (run.input_json or {}).get("retry_scope") == "publish_screening_assessment"
                else "初步筛选评分已启动"
            ),
        )

    def _rebind_repaired_job_profile(self, app: Application) -> None:
        """仅当原冻结画像不可评分时，改绑同一 JD 的当前有效画像。"""

        bound = (
            self.db.get(JobRequirementProfileRecord, app.job_profile_id)
            if app.job_profile_id
            else None
        )
        if evaluate_job_profile_record(
            bound,
            job_id=app.job_id,
            jd_version_id=app.jd_version_id,
        ).ready:
            return

        version = self.db.get(JobVersionRecord, app.jd_version_id)
        active_profile_id = (
            str(version.active_job_profile_id or "") if version is not None else ""
        )
        active = (
            self.db.get(JobRequirementProfileRecord, active_profile_id)
            if active_profile_id
            else None
        )
        readiness = evaluate_job_profile_record(
            active,
            job_id=app.job_id,
            jd_version_id=app.jd_version_id,
        )
        if not readiness.ready:
            raise BusinessError(
                "screening_job_profile_not_ready",
                "岗位画像尚未生成可评分能力，请先重新生成岗位画像。",
                status_code=409,
            )
        app.job_profile_id = active.job_profile_id

    def _enqueue_publish_only_retry(self, *, app: Application) -> WorkflowRun | None:
        """Reuse successful scoring artifacts when only the publish step failed.

        A new WorkflowRun preserves immutable failure history.  Successful prior
        checkpoints are copied as references to the same immutable artifacts, so
        StepRunner starts at publication instead of repeating Miner/LLM scoring.
        """
        original = self.db.scalar(
            select(WorkflowRun)
            .where(
                WorkflowRun.application_id == app.application_id,
                WorkflowRun.workflow_type == "scoring_workflow",
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
        if failed is None or failed.step_name != "publish_screening_assessment":
            return None
        succeeded = list(
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
        # 只复用真实存在且可读取的前置工件。若对象存储中的工件已丢失、引用
        # 不完整或 JSON 已损坏，继续复制检查点只会重复同一个发布错误；此时
        # 返回 None，让调用方创建全新的 V1 Workflow，从当前来源重新计算。
        required_steps = {
            "freeze_screening_sources",
            "score_resume_evidence",
            "score_job_capabilities",
            "assemble_screening_core",
            "derive_screening_rules",
            "generate_screening_presentation",
        }
        if {str(item.step_name) for item in succeeded} != required_steps:
            return None
        artifact_store = WorkflowArtifactStore()
        for checkpoint in succeeded:
            refs = dict(checkpoint.output_refs_json or {})
            artifact_id = str(refs.get("artifactId") or "")
            if not artifact_id:
                return None
            try:
                if not isinstance(artifact_store.get_json(self.db, artifact_id), dict):
                    return None
            except Exception:
                return None
        retry = self.queue.retry(
            original,
            # Checkpoint input hashes include triggered_by. Keeping the original
            # execution identity lets copied checkpoints remain verifiable; the
            # current command actor is still recorded in the audit event below.
            triggered_by=original.triggered_by,
            reason="retry_publish_screening_assessment",
            definition_version=2,
        )
        retry.input_json = {
            **dict(retry.input_json or {}),
            "retry_scope": "publish_screening_assessment",
        }
        for checkpoint in succeeded:
            self.db.add(
                WorkflowStepCheckpoint(
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
                    completed_at=_now(),
                    created_at=_now(),
                    updated_at=_now(),
                )
            )
        return retry

    def enqueue_from_hard_screening_review(self, *, app: Application, user: User) -> WorkflowRun:
        """兼容旧调用；硬筛复核后的内部入队不产生独立事务。"""
        return self.enqueue_in_transaction(
            app=app,
            user=user,
            input_json={},
            source="hard_screening_review",
            allow_resume_restructuring=False,
        ).workflow_run
    def _enqueue_required_resume_restructuring(
        self, app: Application, user: User
    ) -> WorkflowRun | None:
        submission = (
            self.db.get(ResumeSubmission, app.adopted_resume_submission_id)
            if app.adopted_resume_submission_id else None
        )
        if submission is None:
            return None
        metadata = dict(submission.structure_metadata_json or {})
        if submission.structure_schema_version == "resume_structure_result_v3":
            return None
        if submission is None:
            return None
        active = self.queue.active_run(
            workflow_type="resume_document_import_workflow",
            subject_type="resume_submission",
            subject_id=submission.resume_submission_id,
        )
        if active is not None:
            return active
        # 旧 Submission 是不可变处理事实，不能从 completed/failed 原地回到 queued。
        # 复用 Candidate 的正式重解析入口，创建新的版本和新的 WorkflowRun。
        _, run = CandidateResumeRebuildService(self.db).request_reparse(
            submission=submission,
            user=user,
        )
        return run

    def _response(self, app: Application, message: str) -> dict[str, Any]:
        self.db.flush()
        return {"application_id": app.application_id, "status": app.status, "message": message}
