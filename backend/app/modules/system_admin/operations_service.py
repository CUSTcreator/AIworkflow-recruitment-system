from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from backend.app.shared.errors import BusinessRuleError
from backend.app.shared.workflows import StepStatus, WorkflowRunStatus
from backend.app.shared.workflows.status_contracts import (
    ensure_step_transition,
    ensure_workflow_transition,
)
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from backend.app.core.security import hash_password
from backend.app.infrastructure.workflow_runtime import WorkflowQueue
from backend.app.models.entities import (
    Application,
    AuditEvent,
    Department,
    Interview,

    Job,
    JobDocumentImport,
    ResumeSubmission,
    RoleDefinition,
    SourceDocument,
    User,
    UserPermissionOverride,
    WorkflowExecutionEvent,
    WorkflowRun,
    WorkflowStepCheckpoint,
)
from backend.app.modules.candidates.public import CandidateResumeRebuildService
from backend.app.modules.interviews.public import InterviewRecordCommands
from backend.app.modules.interviews.workflows.post_interview_topology_plan import (
    POST_INTERVIEW_TOPOLOGY_DEFINITION_VERSION,
)
from backend.app.modules.applications.public import ApplicationProcessService
from backend.app.modules.document_ingestion.services.job_document_import_process_service import JobDocumentImportProcessService
from backend.app.modules.auth.public import (
    PERMISSION_LABELS,
    ROLE_DEFAULT_PERMISSIONS,
    effective_permissions,
    has_permission,
    permission_overrides,
)
from backend.app.shared.audit import record_audit_event
from backend.app.shared.time_serialization import normalize_utc_iso, utc_iso
from backend.app.storage.object_store import ObjectStore
from backend.app.shared.workflows.process_view import workflow_step_label
from backend.app.modules.system_admin.repository import SystemAdminRepository


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20].upper()}"


def _iso(value: datetime | None) -> str | None:
    return utc_iso(value) if value else None


def _encode_cursor(value: datetime, identifier: str) -> str:
    """将排序键编码为不透明游标，避免管理端大表使用 offset 扫描。"""
    raw = json.dumps({"at": value.isoformat(), "id": identifier}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, str] | None:
    if not cursor.strip():
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        payload = json.loads(raw)
        value = datetime.fromisoformat(str(payload["at"]))
        if value.tzinfo is not None:
            value = value.astimezone(UTC).replace(tzinfo=None)
        identifier = str(payload["id"])
        if not identifier:
            raise ValueError("empty identifier")
        return value, identifier
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise BusinessRuleError(status_code=422, detail="分页游标无效") from error


def _parse_time_filter(value: str, *, field_name: str) -> datetime | None:
    if not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(UTC).replace(tzinfo=None)
        return parsed
    except ValueError as error:
        raise BusinessRuleError(status_code=422, detail=f"{field_name} 时间格式无效") from error


class OperationsAdminService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repository = SystemAdminRepository(db)
        self.queue = WorkflowQueue(db)

    def workflow_summary(self) -> dict[str, Any]:
        counts = {
            status: self.db.scalar(
                select(func.count())
                .select_from(WorkflowRun)
                .where(WorkflowRun.status == status)
            ) or 0
            for status in ("pending", "running", "blocked", "completed", "failed")
        }
        return {
            "pending": counts["pending"],
            "running": counts["running"],
            "blocked": counts["blocked"],
            "completed": counts["completed"],
            "failed": counts["failed"],
        }

    def list_workflows(
        self,
        *,
        status: str = "",
        cursor: str = "",
        limit: int = 50,
    ) -> dict[str, Any]:
        """聚合 WorkflowRun 与最新检查点，供后台任务列表轻量展示。"""
        query = select(WorkflowRun)
        allowed_statuses = {"pending", "running", "blocked", "completed", "failed", "cancelled"}
        status_groups = {
            "attention": ("pending", "running", "blocked", "failed"),
            "finished": ("completed", "cancelled"),
        }
        if status in status_groups:
            query = query.where(WorkflowRun.status.in_(status_groups[status]))
        elif status:
            if status not in allowed_statuses:
                raise BusinessRuleError(status_code=422, detail="后台任务状态筛选无效")
            query = query.where(WorkflowRun.status == status)
        cursor_parts = _decode_cursor(cursor)
        if cursor_parts is not None:
            updated_at, run_id = cursor_parts
            query = query.where(
                or_(
                    WorkflowRun.updated_at < updated_at,
                    and_(WorkflowRun.updated_at == updated_at, WorkflowRun.workflow_run_id < run_id),
                )
            )
        runs = list(self.db.scalars(
            query.order_by(WorkflowRun.updated_at.desc(), WorkflowRun.workflow_run_id.desc()).limit(limit + 1)
        ))
        has_next = len(runs) > limit
        runs = runs[:limit]
        checkpoints_by_run: dict[str, WorkflowStepCheckpoint] = {}
        blocked_run_ids: set[str] = set()
        if runs:
            checkpoints = self.db.scalars(
                select(WorkflowStepCheckpoint)
                .where(WorkflowStepCheckpoint.workflow_run_id.in_([run.workflow_run_id for run in runs]))
                .order_by(
                    WorkflowStepCheckpoint.workflow_run_id,
                    WorkflowStepCheckpoint.step_order.desc(),
                    WorkflowStepCheckpoint.updated_at.desc(),
                )
            )
            for checkpoint in checkpoints:
                checkpoints_by_run.setdefault(checkpoint.workflow_run_id, checkpoint)
                if checkpoint.status == "blocked":
                    blocked_run_ids.add(checkpoint.workflow_run_id)
        # 列表不是完整日志页，只预取每个任务最近一条安全事件，让管理员无需先打开
        # 弹窗也能看到刚发生的解析、重试或发布进度。
        latest_events_by_run: dict[str, WorkflowExecutionEvent] = {}
        if runs:
            latest_events = self.db.scalars(
                select(WorkflowExecutionEvent)
                .where(WorkflowExecutionEvent.workflow_run_id.in_([run.workflow_run_id for run in runs]))
                .order_by(
                    WorkflowExecutionEvent.workflow_run_id,
                    WorkflowExecutionEvent.occurred_at.desc(),
                    WorkflowExecutionEvent.execution_event_id.desc(),
                )
            )
            for event in latest_events:
                latest_events_by_run.setdefault(event.workflow_run_id, event)
        items = []
        active_subjects = {
            (active.workflow_type, *self.queue.subject_for(active))
            for active in self.db.scalars(
                select(WorkflowRun).where(WorkflowRun.status.in_(("pending", "running")))
            )
        }
        for run in runs:
            checkpoint = checkpoints_by_run.get(run.workflow_run_id)
            latest_event = latest_events_by_run.get(run.workflow_run_id)
            safe_error_message = self._safe_workflow_error(run, checkpoint, latest_event)
            available_actions: list[str] = []
            if run.status == "blocked" and run.workflow_run_id in blocked_run_ids:
                available_actions.append("resume")
            elif (
                run.status == "failed"
                and (run.workflow_type, *self.queue.subject_for(run)) not in active_subjects
            ):
                available_actions.append("retry")
            items.append({
                "workflowRunId": run.workflow_run_id,
                "workflowType": run.workflow_type,
                "applicationId": run.application_id,
                "subjectType": run.subject_type,
                "subjectId": run.subject_id,
                "status": run.status,
                "attemptCount": run.attempt_count,
                "maxAttempts": run.max_attempts,
                "startedAt": _iso(run.started_at),
                "completedAt": _iso(run.completed_at),
                "updatedAt": _iso(run.updated_at),
                # error_message 可能包含供应商回包或内部路径，不能成为公开 DTO。
                "errorMessage": safe_error_message,
                "errorCode": checkpoint.last_error_code if checkpoint else None,
                "availableActions": available_actions,
                "currentStepName": checkpoint.step_name if checkpoint else None,
                "currentStepStatus": checkpoint.status if checkpoint else None,
                "nextAttemptAt": _iso(checkpoint.next_attempt_at) if checkpoint else None,
                "latestEventMessage": latest_event.public_message if latest_event else None,
                "latestEventSeverity": latest_event.severity if latest_event else None,
                "latestEventAt": _iso(latest_event.occurred_at) if latest_event else None,
            })
        next_cursor = (
            _encode_cursor(runs[-1].updated_at, runs[-1].workflow_run_id)
            if has_next and runs else None
        )
        return {"items": items, "nextCursor": next_cursor}

    def list_workflow_execution_events(
        self,
        *,
        run_id: str,
        cursor: str = "",
        limit: int = 50,
    ) -> dict[str, Any]:
        """读取不可变时间线，并只投影给管理员安全展示的字段。"""
        if self.repository.workflow(run_id) is None:
            raise BusinessRuleError(status_code=404, detail="Workflow 不存在")
        query = select(WorkflowExecutionEvent).where(
            WorkflowExecutionEvent.workflow_run_id == run_id
        )
        cursor_parts = _decode_cursor(cursor)
        if cursor_parts is not None:
            occurred_at, event_id = cursor_parts
            query = query.where(
                or_(
                    WorkflowExecutionEvent.occurred_at > occurred_at,
                    and_(
                        WorkflowExecutionEvent.occurred_at == occurred_at,
                        WorkflowExecutionEvent.execution_event_id > event_id,
                    ),
                )
            )
        events = list(self.db.scalars(
            query.order_by(
                WorkflowExecutionEvent.occurred_at.asc(),
                WorkflowExecutionEvent.execution_event_id.asc(),
            ).limit(limit + 1)
        ))
        has_next = len(events) > limit
        events = events[:limit]
        items = []
        for event in events:
            diagnostics = event.diagnostic_fields or {}
            items.append({
                "executionEventId": event.execution_event_id,
                "workflowRunId": event.workflow_run_id,
                "occurredAt": _iso(event.occurred_at),
                "stepName": event.step_name,
                "stepLabel": workflow_step_label(event.step_name),
                "eventType": event.event_type,
                "severity": event.severity,
                "message": event.public_message,
                "attemptCount": event.attempt_count,
                "pollCount": event.poll_count,
                "nextAttemptAt": normalize_utc_iso(diagnostics.get("next_attempt_at")) or None,
                "errorCategory": event.error_category,
                "errorCode": event.error_code,
            })
        next_cursor = (
            _encode_cursor(events[-1].occurred_at, events[-1].execution_event_id)
            if has_next and events else None
        )
        return {"items": items, "nextCursor": next_cursor}

    @staticmethod
    def _safe_workflow_error(
        run: WorkflowRun,
        checkpoint: WorkflowStepCheckpoint | None,
        latest_event: WorkflowExecutionEvent | None,
    ) -> str:
        """把内部异常压缩为稳定的管理员提示，原始异常只留在受保护日志中。"""
        if run.status not in {"failed", "blocked"}:
            return ""
        if latest_event is not None and latest_event.severity in {"warning", "error"}:
            return latest_event.public_message
        category = checkpoint.last_error_category if checkpoint else None
        labels = {
            "timeout": "外部服务响应超时，任务未能完成。",
            "rate_limit": "外部服务当前繁忙，任务未能完成。",
            "validation": "外部结果不满足业务要求，任务未能完成。",
            "dependency": "依赖服务暂不可用，任务未能完成。",
        }
        if category in labels:
            return labels[category]
        return "任务等待业务确认。" if run.status == "blocked" else "后台任务执行失败。"

    def retry_workflow(
        self,
        actor: User,
        run_id: str,
        reason: str,
        *,
        commit: bool = True,
    ) -> dict[str, Any]:
        original = self.repository.workflow(run_id)
        if original is None:
            raise BusinessRuleError(status_code=404, detail="Workflow 不存在")
        if original.status not in {"failed", "blocked"}:
            raise BusinessRuleError(status_code=409, detail="只有失败或待确认的 Workflow 可以继续")
        if original.status == "blocked":
            return self._resume_blocked_workflow(actor, original, reason, commit=commit)
        subject_type, subject_id = self.queue.subject_for(original)
        if self.queue.active_run(
            workflow_type=original.workflow_type,
            subject_type=subject_type,
            subject_id=subject_id,
        ):
            raise BusinessRuleError(status_code=409, detail="同一对象已有等待或运行中的任务")
        # 只有“简历导入”本身失败时，才需要创建新版 ResumeSubmission 后重新解析。
        # 候选人岗位分发同样以 resume_submission 为 subject，但其重试必须复用
        # 已完成的结构化简历，只重新执行分发，不能误触发整份简历的重新解析。
        if (
            original.workflow_type == "resume_document_import_workflow"
            and original.subject_type == "resume_submission"
            and original.subject_id
        ):
            return self._retry_resume_submission(actor, original, reason, commit=commit)
        self._restore_retry_subject(original)
        # 管理台重试也必须创建当前已注册的编排版本。初筛 v1 和旧 V2/V3
        # 都不能按已退役的步骤与 Artifact 合同继续恢复。
        current_definition_version = {
            "scoring_workflow": 2,
            "post_first_scoring_workflow": POST_INTERVIEW_TOPOLOGY_DEFINITION_VERSION,
            "post_second_scoring_workflow": POST_INTERVIEW_TOPOLOGY_DEFINITION_VERSION,
        }.get(original.workflow_type)
        retry = self.queue.retry(
            original,
            triggered_by=actor.user_id,
            reason=reason,
            definition_version=current_definition_version,
        )
        record_audit_event(
            self.db,
            actor=actor,
            action="admin.workflow.retry",
            target_type="workflow_run",
            target_id=retry.workflow_run_id,
            summary=f"人工重试 {original.workflow_type}",
            details={"retryOf": original.workflow_run_id, "reason": reason},
        )
        self._finish(commit)
        return {
            "workflowRunId": retry.workflow_run_id,
            "status": retry.status,
            "retryOf": original.workflow_run_id,
        }

    def _retry_resume_submission(
        self,
        actor: User,
        original: WorkflowRun,
        reason: str,
        *,
        commit: bool,
    ) -> dict[str, Any]:
        """管理员重试简历时复用正式重解析命令，保留旧 Submission 审计事实。"""
        submission = self.repository.resume_submission(str(original.subject_id or ""))
        if submission is None:
            raise BusinessRuleError(status_code=404, detail="简历处理任务不存在")
        next_submission, retry = CandidateResumeRebuildService(self.db).request_reparse(
            submission=submission,
            user=actor,
        )
        record_audit_event(
            self.db,
            actor=actor,
            action="admin.workflow.retry",
            target_type="workflow_run",
            target_id=retry.workflow_run_id,
            summary=f"人工重新解析简历：{original.workflow_type}",
            details={
                "retryOf": original.workflow_run_id,
                "reason": reason,
                "supersedesSubmissionId": submission.resume_submission_id,
                "resumeSubmissionId": next_submission.resume_submission_id,
            },
        )
        self._finish(commit)
        return {
            "workflowRunId": retry.workflow_run_id,
            "status": retry.status,
            "retryOf": original.workflow_run_id,
        }
    def _resume_blocked_workflow(
        self,
        actor: User,
        original: WorkflowRun,
        reason: str,
        *,
        commit: bool = True,
    ) -> dict[str, Any]:
        """人工条件已满足后恢复同一检查点，不重跑此前已成功的步骤。"""
        checkpoint = self.db.scalar(
            select(WorkflowStepCheckpoint)
            .where(
                WorkflowStepCheckpoint.workflow_run_id == original.workflow_run_id,
                WorkflowStepCheckpoint.status == "blocked",
            )
            .order_by(WorkflowStepCheckpoint.step_order.desc())
            .with_for_update()
        )
        if checkpoint is None:
            raise BusinessRuleError(status_code=409, detail="该阻塞任务缺少可恢复的步骤检查点")
        now = datetime.now(UTC).replace(tzinfo=None)
        ensure_step_transition(checkpoint.status, StepStatus.PENDING)
        checkpoint.status = StepStatus.PENDING.value
        checkpoint.completed_at = None
        checkpoint.next_attempt_at = now
        checkpoint.lease_owner = None
        checkpoint.lease_expires_at = None
        checkpoint.updated_at = now
        ensure_workflow_transition(original.status, WorkflowRunStatus.PENDING)
        original.status = WorkflowRunStatus.PENDING.value
        original.available_at = now
        original.completed_at = None
        original.error_message = None
        original.lease_owner = None
        original.lease_expires_at = None
        original.heartbeat_at = now
        original.updated_at = now
        record_audit_event(
            self.db,
            actor=actor,
            action="admin.workflow.resume_blocked",
            target_type="workflow_run",
            target_id=original.workflow_run_id,
            summary=f"继续阻塞任务 {original.workflow_type}",
            details={"reason": reason, "checkpoint": checkpoint.step_name},
        )
        self._finish(commit)
        return {
            "workflowRunId": original.workflow_run_id,
            "status": original.status,
            "resumedStep": checkpoint.step_name,
        }
    def _finish(self, commit: bool) -> None:
        # 保留参数仅兼容旧内部调用；提交权只属于外层 CommandRunner。
        self.db.flush()

    def list_audit_events(
        self,
        *,
        action: str = "",
        category: str = "",
        query_text: str = "",
        actor_user_id: str = "",
        from_at: str = "",
        to_at: str = "",
        cursor: str = "",
        limit: int = 50,
    ) -> dict[str, Any]:
        """按服务端筛选和游标分页读取业务审计事件。"""
        query = select(AuditEvent).where(AuditEvent.action != "auth.login")
        if action.strip():
            query = query.where(AuditEvent.action.ilike(f"%{action.strip()}%"))
        category_filters = {
            "account": or_(AuditEvent.action.like("admin.user.%"), AuditEvent.action.like("admin.role.%")),
            "organization": or_(
                AuditEvent.action.like("admin.department.%"),
                AuditEvent.action.like("admin.job.%"),
                AuditEvent.action == "job.delete",
            ),
            "task": AuditEvent.action.like("admin.workflow.%"),
        }
        if category.strip():
            if category == "recruitment":
                query = query.where(~or_(*category_filters.values()))
            elif category in category_filters:
                query = query.where(category_filters[category])
            else:
                raise BusinessRuleError(status_code=422, detail="操作日志分类无效")
        if query_text.strip():
            needle = f"%{query_text.strip()}%"
            query = query.where(or_(
                AuditEvent.actor_name.ilike(needle),
                AuditEvent.action.ilike(needle),
                AuditEvent.target_type.ilike(needle),
                AuditEvent.target_id.ilike(needle),
                AuditEvent.summary.ilike(needle),
            ))
        if actor_user_id.strip():
            query = query.where(AuditEvent.actor_user_id == actor_user_id.strip())
        from_value = _parse_time_filter(from_at, field_name="起始")
        to_value = _parse_time_filter(to_at, field_name="结束")
        if from_value is not None:
            query = query.where(AuditEvent.created_at >= from_value)
        if to_value is not None:
            query = query.where(AuditEvent.created_at <= to_value)
        cursor_parts = _decode_cursor(cursor)
        if cursor_parts is not None:
            created_at, event_id = cursor_parts
            query = query.where(
                or_(
                    AuditEvent.created_at < created_at,
                    and_(AuditEvent.created_at == created_at, AuditEvent.audit_event_id < event_id),
                )
            )
        events = list(self.db.scalars(
            query.order_by(AuditEvent.created_at.desc(), AuditEvent.audit_event_id.desc()).limit(limit + 1)
        ))
        has_next = len(events) > limit
        events = events[:limit]
        items = [{
            "auditEventId": event.audit_event_id,
            "actorUserId": event.actor_user_id,
            "actorName": event.actor_name,
            "action": event.action,
            "targetType": event.target_type,
            "targetId": event.target_id,
            "summary": event.summary,
            "details": self._public_audit_details(event.details or {}),
            "createdAt": _iso(event.created_at),
        } for event in events]
        next_cursor = (
            _encode_cursor(events[-1].created_at, events[-1].audit_event_id)
            if has_next and events else None
        )
        return {"items": items, "nextCursor": next_cursor}

    def list_object_cleanup_tasks(self) -> list[dict[str, Any]]:
        """列出永久删除后仍待清理的对象；内部对象引用不越过服务边界。"""
        events = self.db.scalars(
            select(AuditEvent)
            .where(AuditEvent.action == "application.hard_delete")
            .order_by(AuditEvent.created_at.desc())
        ).all()
        tasks: list[dict[str, Any]] = []
        for event in events:
            details = event.details or {}
            status = details.get("objectCleanupStatus")
            refs = details.get("_objectCleanupRefs")
            if status not in {"pending", "failed"} or not isinstance(refs, list) or not refs:
                continue
            tasks.append({
                "cleanupTaskId": event.audit_event_id,
                "applicationId": event.target_id,
                "status": status,
                "pendingObjectCount": len(refs),
                "lastAttemptAt": normalize_utc_iso(details.get("objectCleanupLastAttemptAt")) or None,
                "message": "文件清理尚未完成，可安全重试。",
            })
        return tasks

    def request_object_cleanup_retry(
        self,
        actor: User,
        cleanup_task_id: str,
        *,
        commit: bool = True,
    ) -> dict[str, Any]:
        event = self.db.scalar(
            select(AuditEvent)
            .where(
                AuditEvent.audit_event_id == cleanup_task_id,
                AuditEvent.action == "application.hard_delete",
            )
            .with_for_update()
        )
        details = dict(event.details or {}) if event else {}
        refs = details.get("_objectCleanupRefs")
        if event is None or not isinstance(refs, list) or not refs:
            raise BusinessRuleError(status_code=409, detail="该文件清理任务已完成或不存在")
        details["objectCleanupStatus"] = "pending"
        event.details = details
        record_audit_event(
            self.db,
            actor=actor,
            action="admin.object_cleanup.retry",
            target_type="object_cleanup_task",
            target_id=cleanup_task_id,
            summary=f"重试申请文件清理：{event.target_id}",
            details={"applicationId": event.target_id, "pendingObjectCount": len(refs)},
        )
        self._finish(commit)
        return {"cleanupTaskId": cleanup_task_id, "status": "pending"}

    def execute_object_cleanup(self, cleanup_task_id: str) -> dict[str, Any]:
        """执行幂等对象删除并独立提交结果，供数据库提交后的回调调用。"""
        event = self.db.scalar(
            select(AuditEvent)
            .where(
                AuditEvent.audit_event_id == cleanup_task_id,
                AuditEvent.action == "application.hard_delete",
            )
            .with_for_update()
        )
        if event is None:
            return {"status": "completed", "pendingObjectCount": 0}
        details = dict(event.details or {})
        refs = details.get("_objectCleanupRefs")
        pending_refs = [str(ref) for ref in refs] if isinstance(refs, list) else []
        remaining: list[str] = []
        store = ObjectStore()
        for object_ref in pending_refs:
            try:
                store.delete_object(object_ref)
            except Exception:
                remaining.append(object_ref)
        attempted_at = datetime.now(UTC).replace(tzinfo=None)
        details.update({
            "objectCleanupStatus": "failed" if remaining else "completed",
            "objectCleanupPendingCount": len(remaining),
            "objectCleanupLastAttemptAt": utc_iso(attempted_at),
            "_objectCleanupRefs": remaining,
        })
        event.details = details
        self.db.commit()
        return {
            "status": details["objectCleanupStatus"],
            "pendingObjectCount": len(remaining),
        }

    @staticmethod
    def _public_audit_details(details: dict[str, Any]) -> dict[str, Any]:
        """审计页可展示状态与计数，但不得暴露内部对象存储引用。"""
        return {
            key: value
            for key, value in details.items()
            if not key.startswith("_")
        }

    def _restore_retry_subject(self, run: WorkflowRun) -> None:
        if run.workflow_type == "scoring_workflow" and run.application_id:
            app = self.repository.application(run.application_id)
            if app and app.status == "screening_failed":
                ApplicationProcessService.transition(
                    app,
                    action="retry_scoring",
                    now=datetime.now(UTC).replace(tzinfo=None),
                    owner="系统管理员",
                )
        elif run.workflow_type == "hard_screening_workflow" and run.application_id:
            app = self.repository.application(run.application_id)
            if app:
                ApplicationProcessService.transition(
                    app,
                    action="retry_hard_screening",
                    now=datetime.now(UTC).replace(tzinfo=None),
                    owner="系统管理员",
                )
        elif run.workflow_type in {"post_first_scoring_workflow", "post_second_scoring_workflow"} and run.application_id:
            # V2/V3 的最终失败会把面试和待办标记为失败；管理员重新入队时，
            # 恢复为“已提交、正在重新计算”，但不改写已有原始面评记录。
            app = self.repository.application(run.application_id)
            if app:
                first = run.workflow_type == "post_first_scoring_workflow"
                interview_id = f"INT_{app.application_id}_{'FIRST' if first else 'SECOND'}"
                interview = self.db.get(Interview, interview_id)
                if interview is not None:
                    interview.status = "submitted"
                InterviewRecordCommands(self.db).mark_task_processing(
                    app.application_id,
                    "conduct_first_interview" if first else "conduct_second_interview",
                    "正在重新计算一面后评估" if first else "正在重新计算二面后评估",
                )
        elif run.subject_type == "job_document_import" and run.subject_id:
            import_task = self.db.get(JobDocumentImport, run.subject_id)
            if import_task:
                JobDocumentImportProcessService.queue(
                    import_task,
                    workflow_run_id=run.workflow_run_id,
                    now=datetime.now(UTC).replace(tzinfo=None),
                )
