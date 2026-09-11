"""Application 生命周期命令：处理申请删除及其关联数据清理。"""
from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.orm import Session

from backend.app.db.session import Base
from backend.app.modules.applications.repository import ApplicationRepository
from backend.app.models.entities import (
    Application,
    ApplicationDocumentLink,
    CandidateDocumentLink,
    Candidate,
    Job,
    ResumeSubmission,
    SourceDocument,
    Task,
    User,
    WorkflowArtifact,
    WorkflowActivityCheckpoint,
    WorkflowExecutionEvent,
    WorkflowRun,
    WorkflowStepCheckpoint,
)
from backend.app.shared.audit import record_audit_event
from backend.app.modules.auth.public import AuthorizationService
from backend.app.shared.errors import BusinessError
from backend.app.shared.time_serialization import utc_iso



class ApplicationLifecycleCommands:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repository = ApplicationRepository(db)

    def soft_delete(
        self,
        actor: User,
        application_id: str,
    ) -> dict[str, Any]:
        app = self.repository.get_for_update(application_id)
        if app is None:
            raise BusinessError("application_not_found", "未找到候选申请", status_code=404)
        AuthorizationService(self.db).require_business_action(
            actor, "application.delete", department_id=app.department_id
        )
        if app.deleted_at is not None:
            return {
                "applicationId": application_id,
                "deleted": True,
                "deletedAt": utc_iso(app.deleted_at),
            }

        now = datetime.now(UTC).replace(tzinfo=None)
        app.deleted_at = now
        app.deleted_by_user_id = actor.user_id
        app.updated_at = now
        self.db.execute(
            update(Task)
            .where(
                Task.application_id == application_id,
                Task.status.in_(("pending", "in_progress")),
            )
            .values(status="done", completed_at=now, updated_at=now)
        )
        self.db.execute(
            update(WorkflowRun)
            .where(
                WorkflowRun.application_id == application_id,
                WorkflowRun.status.in_(("pending", "running")),
            )
            .values(
                status="cancelled",
                completed_at=now,
                error_message="申请已删除，后台任务已取消",
                lease_owner=None,
                lease_expires_at=None,
                heartbeat_at=now,
                updated_at=now,
            )
        )
        record_audit_event(
            self.db,
            actor=actor,
            action="application.delete",
            target_type="application",
            target_id=application_id,
            summary=f"删除岗位申请 {application_id}",
            details={
                "candidateId": app.candidate_id,
                "jobId": app.job_id,
                "deleteMode": "soft",
            },
        )
        # 提交由外层 CommandRunner 统一完成，保证审计、幂等记录和状态变更同事务发布。
        self.db.flush()
        return {
            "applicationId": application_id,
            "deleted": True,
            "deletedAt": utc_iso(now),
        }

    def list_deleted(self, *, cursor: str = "", limit: int = 50) -> dict[str, Any]:
        deleted_by = User.__table__.alias("deleted_by")
        query = (
            select(Application, Candidate, Job, deleted_by.c.display_name)
            .outerjoin(Candidate, Candidate.candidate_id == Application.candidate_id)
            .join(Job, Job.job_id == Application.job_id)
            .outerjoin(
                deleted_by,
                deleted_by.c.user_id == Application.deleted_by_user_id,
            )
            .where(Application.deleted_at.is_not(None))
        )
        cursor_parts = _decode_deleted_cursor(cursor)
        if cursor_parts is not None:
            deleted_at, application_id = cursor_parts
            query = query.where(or_(
                Application.deleted_at < deleted_at,
                and_(
                    Application.deleted_at == deleted_at,
                    Application.application_id < application_id,
                ),
            ))
        rows = self.db.execute(
            query.order_by(
                Application.deleted_at.desc(), Application.application_id.desc()
            ).limit(limit + 1)
        ).all()
        has_next = len(rows) > limit
        rows = rows[:limit]
        items = [
            {
                "applicationId": app.application_id,
                "candidateName": candidate.display_name if candidate else "待识别候选人",
                "jobTitle": job.title,
                "departmentId": app.department_id,
                "deletedAt": utc_iso(app.deleted_at) if app.deleted_at else None,
                "deletedBy": deleted_by_name or "",
            }
            for app, candidate, job, deleted_by_name in rows
        ]
        next_cursor = None
        if has_next and rows:
            last_app = rows[-1][0]
            next_cursor = _encode_deleted_cursor(
                last_app.deleted_at, last_app.application_id
            )
        return {"items": items, "nextCursor": next_cursor}

    def hard_delete(self, actor: User, application_id: str) -> dict[str, Any]:
        app = self.repository.get_for_update(application_id)
        if app is None:
            raise BusinessError("application_not_found", "未找到候选申请", status_code=404)
        if app.deleted_at is None:
            raise BusinessError(
                "application_must_be_soft_deleted",
                "必须先删除申请，才能执行彻底删除",
                status_code=409,
            )

        candidate_id = app.candidate_id
        job_id = app.job_id
        source_document_ids = set(
            self.db.scalars(
                select(ResumeSubmission.source_document_id).where(
                    ResumeSubmission.application_id == application_id
                )
            )
        )
        source_document_ids.update(
            self.db.scalars(
                select(ApplicationDocumentLink.source_document_id).where(
                    ApplicationDocumentLink.application_id == application_id
                )
            )
        )
        submission_ids = set(
            self.db.scalars(
                select(ResumeSubmission.resume_submission_id).where(
                    ResumeSubmission.application_id == application_id
                )
            )
        )
        object_refs = {
            item
            for item in self.db.scalars(
                select(WorkflowArtifact.object_ref).where(
                    WorkflowArtifact.application_id == application_id,
                    WorkflowArtifact.object_ref.is_not(None),
                )
            )
            if item
        }

        deletion_audit = record_audit_event(
            self.db,
            actor=actor,
            action="application.hard_delete",
            target_type="application",
            target_id=application_id,
            summary=f"彻底删除岗位申请 {application_id}",
            details={"jobId": job_id, "deleteMode": "hard"},
        )

        indirect_run_ids: set[str] = set()
        if submission_ids:
            indirect_run_ids = set(
                self.db.scalars(
                    select(WorkflowRun.workflow_run_id).where(
                        WorkflowRun.subject_type == "resume_submission",
                        WorkflowRun.subject_id.in_(submission_ids),
                    )
                )
            )
        direct_run_ids = set(self.db.scalars(
            select(WorkflowRun.workflow_run_id).where(
                WorkflowRun.application_id == application_id
            )
        ))
        application_run_ids = direct_run_ids | indirect_run_ids

        # application_id 覆盖不到 Step/Activity 检查点。先清理这些只按 run_id
        # 关联的执行账本，否则 PostgreSQL 会拒绝删除真实执行过的 WorkflowRun。
        self._delete_workflow_run_children(application_run_ids)

        # 明确列出 Application 聚合内的删除顺序。模型中 Application、Candidate、
        # ResumeSubmission 与 WorkflowRun 存在循环外键，不能依赖 sorted_tables 猜顺序。
        # ResumeSubmission 属于候选人简历历史，仅解除旧 application_id，不随申请销毁。
        for table_name in (
            "assessment_topology_snapshots",
            "interview_guides",
            "interview_questions",
            "first_interview_plan_versions",
            "assessment_topology_definitions",
            "score_snapshots",
            "hard_screening_results",
            "screening_assessments",
            "interview_targets",
            "human_decisions",
            "stage_history",
            "tasks",
            "interview_records",
            "candidate_capability_profiles",
            "application_assessment_versions",
            "interview_parse_results",
            "interviews",
            "application_workspace_documents",
        ):
            table = Base.metadata.tables.get(table_name)
            if table is not None:
                self.db.execute(delete(table).where(table.c.application_id == application_id))
        self.db.execute(
            update(ResumeSubmission)
            .where(ResumeSubmission.application_id == application_id)
            .values(application_id=None, routing_workflow_run_id=None)
        )
        if application_run_ids:
            self.db.execute(
                delete(WorkflowRun).where(WorkflowRun.workflow_run_id.in_(application_run_ids))
            )
        self.db.execute(
            delete(Application).where(Application.application_id == application_id)
        )

        remaining_candidate_references = 0
        if candidate_id:
            remaining_candidate_references = int(
                self.db.scalar(select(func.count()).select_from(Application).where(Application.candidate_id == candidate_id)) or 0
            ) + int(
                self.db.scalar(select(func.count()).select_from(ResumeSubmission).where(ResumeSubmission.candidate_id == candidate_id)) or 0
            ) + int(
                self.db.scalar(select(func.count()).select_from(CandidateDocumentLink).where(CandidateDocumentLink.candidate_id == candidate_id)) or 0
            )
        if candidate_id and remaining_candidate_references == 0:
            resume_profiles = Base.metadata.tables.get("resume_profiles")
            if resume_profiles is not None:
                self.db.execute(
                    delete(resume_profiles).where(
                        resume_profiles.c.candidate_id == candidate_id
                    )
                )
            self.db.execute(
                delete(Candidate).where(Candidate.candidate_id == candidate_id)
            )

        removable_document_ids: list[str] = []
        for document_id in source_document_ids:
            if not document_id:
                continue
            reference_count = self.repository.source_document_reference_count(document_id)
            if reference_count != 0:
                continue
            document = self.db.get(SourceDocument, document_id)
            if document is None:
                continue
            object_refs.add(document.object_ref)
            object_refs.update(
                value for value in (
                    document.document_blocks_ref,
                ) if value
            )
            removable_document_ids.append(document_id)

        if removable_document_ids:
            source_run_ids = set(
                self.db.scalars(
                    select(WorkflowRun.workflow_run_id).where(
                        WorkflowRun.subject_type == "source_document",
                        WorkflowRun.subject_id.in_(removable_document_ids),
                    )
                )
            )
            if source_run_ids:
                self.db.execute(
                    delete(WorkflowArtifact).where(WorkflowArtifact.workflow_run_id.in_(source_run_ids))
                )
                self._delete_workflow_run_children(source_run_ids)
                self.db.execute(delete(WorkflowRun).where(WorkflowRun.workflow_run_id.in_(source_run_ids)))
            self.db.execute(
                delete(SourceDocument).where(
                    SourceDocument.source_document_id.in_(removable_document_ids)
                )
            )

        cleanup_refs = sorted(item for item in object_refs if item)
        # 对象删除必须等数据库事务提交后执行；待清理引用写入保留的审计事件，
        # 即使进程在提交后退出，管理员仍能在系统管理页继续清理。
        deletion_audit.details = {
            **(deletion_audit.details or {}),
            "objectCleanupStatus": "pending" if cleanup_refs else "completed",
            "objectCleanupPendingCount": len(cleanup_refs),
            "_objectCleanupRefs": cleanup_refs,
        }
        self.db.flush()
        return {
            "applicationId": application_id,
            "permanentlyDeleted": True,
            "cleanupWarnings": [],
            "cleanupTaskId": deletion_audit.audit_event_id if cleanup_refs else None,
            "_cleanup_audit_event_id": deletion_audit.audit_event_id,
        }

    def _delete_workflow_run_children(self, run_ids: set[str]) -> None:
        """按外键依赖顺序删除运行时账本；领域表由所属聚合的删除逻辑处理。"""
        if not run_ids:
            return
        for model in (
            WorkflowActivityCheckpoint,
            WorkflowStepCheckpoint,
            WorkflowExecutionEvent,
            WorkflowArtifact,
        ):
            self.db.execute(delete(model).where(model.workflow_run_id.in_(run_ids)))


def _encode_deleted_cursor(value: datetime, application_id: str) -> str:
    payload = json.dumps(
        {"at": value.isoformat(), "id": application_id}, separators=(",", ":")
    )
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_deleted_cursor(cursor: str) -> tuple[datetime, str] | None:
    if not cursor.strip():
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        value = datetime.fromisoformat(str(payload["at"]))
        if value.tzinfo is not None:
            value = value.astimezone(UTC).replace(tzinfo=None)
        application_id = str(payload["id"])
        if not application_id:
            raise ValueError("empty application id")
        return value, application_id
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise BusinessError(
            "invalid_deleted_application_cursor",
            "已删除申请分页游标无效",
            status_code=422,
        ) from error
