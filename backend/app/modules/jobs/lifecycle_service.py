from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json
from uuid import uuid4
from typing import Any

from backend.app.shared.errors import BusinessRuleError
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from backend.app.models.entities import (
    Application,
    Job,
    JobDraft,
    JobVersionRecord,
    StageHistory,
    Task,
    User,
    WorkflowRun,
)
from backend.app.modules.applications.public import ApplicationProcessService
from backend.app.modules.applications.public import (
    blocks_owner_deletion,
    can_cancel_for_owner_deletion,
)
from backend.app.shared.audit import record_audit_event
from backend.app.shared.time_serialization import utc_iso
from backend.app.modules.auth.public import assert_business_action
from backend.app.modules.jobs.services.job_profile_service import JobProfileService
from backend.app.modules.jobs.job_process_service import JobProcessService


class JobLifecycleService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def soft_delete(self, actor: User, job_id: str) -> dict[str, Any]:
        job = self.db.scalar(select(Job).where(Job.job_id == job_id).with_for_update())
        if job is None or job.deleted_at is not None:
            raise BusinessRuleError(status_code=404, detail="岗位不存在")
        if not actor.is_system_admin:
            assert_business_action(
                self.db,
                actor,
                "job.delete",
                department_id=job.department_id,
            )
        now = _now()
        self.soft_delete_jobs(actor, [job], now=now, reason="job_deleted")
        # 由外层命令统一提交，以便岗位、申请取消、审计和幂等记录原子发布。
        self.db.flush()
        return {"jobId": job.job_id, "deleted": True, "deletedAt": utc_iso(now)}

    def soft_delete_jobs(
        self,
        actor: User,
        jobs: list[Job],
        *,
        now: datetime,
        reason: str,
    ) -> None:
        job_ids = [job.job_id for job in jobs if job.deleted_at is None]
        if not job_ids:
            return
        applications = self.db.scalars(
            select(Application)
            .where(
                Application.job_id.in_(job_ids),
                Application.deleted_at.is_(None),
            )
            .with_for_update()
        ).all()
        source_statuses = self._review_source_statuses(applications)
        blocking = [
            app for app in applications
            if blocks_owner_deletion(app.status, source_status=source_statuses.get(app.application_id))
        ]
        if blocking:
            raise BusinessRuleError(
                status_code=409,
                detail=f"存在 {len(blocking)} 个已进入面试或最终决策的申请，不能删除",
            )
        cancellable = [
            app for app in applications
            if can_cancel_for_owner_deletion(app.status, source_status=source_statuses.get(app.application_id))
        ]
        self._cancel_early_applications(cancellable, now=now, reason=reason)
        for job in jobs:
            if job.deleted_at is not None:
                continue
            # 行政软删除独立于招聘状态机：setup_pending 从未开放，
            # 只需写 deleted_at，不能伪造一次关闭招聘的业务转换。
            if job.status == "open":
                JobProcessService.close_job(job, now=now)
            elif job.status == "closed":
                job.closed_at = job.closed_at or now
            elif job.status == "setup_pending":
                job.closed_at = None
            else:
                raise BusinessRuleError(status_code=409, detail=f"岗位状态 {job.status} 不允许删除")
            job.deleted_at = now
            job.deleted_by_user_id = actor.user_id
            # 删除时间和操作者已有具名列；原因只写 AuditEvent，不能回填到业务 payload。
            record_audit_event(
                self.db,
                actor=actor,
                action="job.delete",
                target_type="job",
                target_id=job.job_id,
                summary=f"删除岗位：{job.title}",
                details={"departmentId": job.department_id, "deleteMode": "soft", "reason": reason},
            )

    def _review_source_statuses(self, applications: list[Application]) -> dict[str, str]:
        review_ids = [
            app.application_id for app in applications
            if app.status in {"on_hold", "manual_review"}
        ]
        if not review_ids:
            return {}
        rows = self.db.scalars(
            select(StageHistory)
            .where(StageHistory.application_id.in_(review_ids))
            .order_by(StageHistory.created_at.desc())
        ).all()
        sources: dict[str, str] = {}
        current = {app.application_id: app.status for app in applications}
        for row in rows:
            if row.application_id not in sources and row.to_status == current.get(row.application_id):
                sources[row.application_id] = row.from_status
        return sources

    def _cancel_early_applications(
        self,
        applications: list[Application],
        *,
        now: datetime,
        reason: str,
    ) -> None:
        application_ids = [app.application_id for app in applications]
        for app in applications:
            ApplicationProcessService.transition(
                app, action="cancel_recruitment", now=now, owner="系统"
            )
        if not application_ids:
            return
        self.db.execute(
            update(Task)
            .where(Task.application_id.in_(application_ids), Task.status.in_(("pending", "in_progress")))
            .values(status="done", completed_at=now, updated_at=now)
        )
        self.db.execute(
            update(WorkflowRun)
            .where(
                WorkflowRun.application_id.in_(application_ids),
                WorkflowRun.status.in_(("pending", "running")),
            )
            .values(
                status="cancelled",
                completed_at=now,
                error_message="岗位或部门已删除，后台任务已取消",
                lease_owner=None,
                lease_expires_at=None,
                heartbeat_at=now,
                updated_at=now,
            )
        )


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)

class JobEditingService:
    """Updates the current requisition without changing historical JD versions."""

    def __init__(self, db: Session) -> None:
        self.db = db

    @staticmethod
    def _clean_lines(values: list[str]) -> list[str]:
        result: list[str] = []
        for value in values:
            item = str(value or "").strip()
            if item and item not in result:
                result.append(item)
        return result

    def update(self, actor: User, job_id: str, data: dict[str, Any]) -> Job:
        job = self.db.scalar(select(Job).where(Job.job_id == job_id).with_for_update())
        if job is None or job.deleted_at is not None:
            raise BusinessRuleError(status_code=404, detail="岗位不存在")
        assert_business_action(self.db, actor, "job.edit", department_id=job.department_id)

        title = str(data["title"]).strip()
        jd_text = str(data["jd_text"]).strip()
        responsibilities = self._clean_lines(list(data.get("responsibilities") or []))
        qualifications = self._clean_lines(list(data.get("qualifications") or []))
        education_requirement = str(data.get("education_requirement") or "").strip() or None
        major_requirement = str(data.get("major_requirement") or "").strip() or None
        headcount = data.get("headcount")
        if not title or not jd_text:
            raise BusinessRuleError(status_code=422, detail="岗位名称和完整岗位要求不能为空")

        version_payload = {
            "title": title,
            "headcount": headcount,
            "jd_text": jd_text,
            "responsibilities": responsibilities,
            "qualifications": qualifications,
            "education_requirement": education_requirement,
            "major_requirement": major_requirement,
        }
        source_sha256 = sha256(
            json.dumps(version_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        now = datetime.now(UTC).replace(tzinfo=None)
        job.title = title
        job.headcount = headcount
        job.jd_text = jd_text
        job.responsibilities = responsibilities
        job.qualifications = qualifications
        job.education_requirement = education_requirement
        job.major_requirement = major_requirement
        job.jd_content_sha256 = source_sha256

        existing = self.db.scalar(
            select(JobVersionRecord).where(
                JobVersionRecord.job_id == job_id,
                JobVersionRecord.source_sha256 == source_sha256,
            )
        )
        if existing is None:
            latest_version = self.db.scalar(
                select(JobVersionRecord.version)
                .where(JobVersionRecord.job_id == job_id)
                .order_by(JobVersionRecord.version.desc())
                .limit(1)
            ) or 0
            version = JobVersionRecord(
                jd_version_id=f"JDV_{uuid4().hex[:20].upper()}",
                job_id=job_id,
                version=int(latest_version) + 1,
                source_sha256=source_sha256,
                source_text=jd_text,
                frozen_job_json={
                    "title": job.title,
                    "department_id": job.department_id,
                    "headcount": job.headcount,
                    "source_document_id": job.source_document_id,
                    "jd_text": job.jd_text,
                    "responsibilities": list(job.responsibilities or []),
                    "qualifications": list(job.qualifications or []),
                    "education_requirement": job.education_requirement,
                    "major_requirement": job.major_requirement,

                },
                preset_model_id=job.preset_model_id,
                preset_model_version=job.preset_model_version,
                job_capability_algorithm_version="job_capability_v1.0",
                profile_status="queued",
                created_at=now,
            )
            self.db.add(version)
            self.db.flush()
            # 岗位编辑产生了新的冻结 JD，必须在同一事务内创建对应画像任务。
            JobProfileService(self.db).enqueue_for_version(
                version,
                triggered_by=actor.user_id,
            )
        record_audit_event(
            self.db,
            actor=actor,
            action="job.edit",
            target_type="job",
            target_id=job_id,
            summary=f"编辑岗位：{title}",
            details={"departmentId": job.department_id, "jdVersionChanged": existing is None},
        )
        # CommandRunner 将在审计与幂等记录写入后统一提交。
        self.db.flush()
        return job
