"""Application 投递命令：创建并冻结 Candidate、简历版本与岗位版本。"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from backend.app.models.entities import (
    Application,
    Candidate,
    Job,
    JobRequirementProfileRecord,
    JobVersionRecord,
    ResumeProfileRecord,
    SourceDocument,
    StageHistory,
    User,
)
from backend.app.modules.applications.application_process_service import ApplicationProcessService
from backend.app.modules.applications.domain.application_state_machine import WAITING_JOB_PROFILE_STATUS
from backend.app.modules.jobs.public import evaluate_job_profile_record
from backend.app.shared.errors import BusinessError


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12].upper()}"


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class ApplicationIntakeCommands:
    """根据已完成的 Candidate 与 ResumeProfile 创建独立岗位申请。"""

    def __init__(self, db: Session) -> None:
        self.db = db

    def create_from_candidate(
        self,
        *,
        user: User,
        candidate: Candidate,
        profile: ResumeProfileRecord,
        job: Job | None,
        job_version: JobVersionRecord,
        document: SourceDocument,
        frozen_job_profile_id: str | None = None,
        submitted_at: datetime,
    ) -> Application:
        """创建 Application；岗位画像未就绪时只进入等待态，不拒绝分发。"""
        if job is None or job.deleted_at is not None:
            raise BusinessError("job_not_found", "岗位不存在或尚未确认", status_code=404)
        if job.status not in {"setup_pending", "open"}:
            raise BusinessError("job_not_routable", "岗位已关闭，不能创建新的岗位申请", status_code=409)
        if profile.candidate_id != candidate.candidate_id:
            raise BusinessError("resume_profile_candidate_mismatch", "简历画像不属于当前候选人", status_code=409)
        if job_version.job_id != job.job_id:
            raise BusinessError("job_version_job_mismatch", "岗位版本不属于当前岗位", status_code=409)

        existing = self.db.query(Application).filter(
            Application.candidate_id == candidate.candidate_id,
            Application.job_id == job.job_id,
            Application.deleted_at.is_(None),
        ).one_or_none()
        if existing is not None:
            return existing

        # 专业匹配并不依赖岗位能力画像；Application 创建时才冻结当前可评分画像。
        # 历史空画像即使仍挂在 active 指针上，也必须按“画像未就绪”处理。
        candidate_profile_ids = list(dict.fromkeys(
            profile_id
            for profile_id in (
                frozen_job_profile_id,
                job_version.active_job_profile_id,
            )
            if profile_id
        ))
        job_profile = None
        for selected_profile_id in candidate_profile_ids:
            candidate_profile = self.db.get(
                JobRequirementProfileRecord, selected_profile_id
            )
            if candidate_profile is not None and (
                candidate_profile.job_id != job.job_id
                or candidate_profile.jd_version_id != job_version.jd_version_id
            ):
                raise BusinessError(
                    "job_profile_version_mismatch",
                    "岗位当前能力画像与冻结 JD 版本不一致",
                    status_code=409,
                )
            readiness = evaluate_job_profile_record(
                candidate_profile,
                job_id=job.job_id,
                jd_version_id=job_version.jd_version_id,
            )
            if readiness.ready:
                job_profile = candidate_profile
                break

        initial_status = (
            ApplicationProcessService.initial_status()
            if job_profile is not None
            else WAITING_JOB_PROFILE_STATUS
        )
        application = Application(
            application_id=_id("APP"),
            candidate_id=candidate.candidate_id,
            job_id=job.job_id,
            jd_version_id=job_version.jd_version_id,
            job_profile_id=job_profile.job_profile_id if job_profile is not None else None,
            source_resume_profile_id=profile.resume_profile_id,
            adopted_resume_submission_id=candidate.current_resume_submission_id,
            status=initial_status,
            department_id=job.department_id,
            current_owner="系统",
            assigned_first_interviewer=job.department_recruiter_id,
            # 每个 Application 必须在创建时确定 HR 负责人；后续 V2 发布、二面和最终审核
            # 都依赖这个明确负责人创建待办，不能把空值留到异步发布阶段才暴露。
            assigned_hr=self._default_hr_assignee_id(),
            submitted_at=submitted_at,
            updated_at=_now(),
            due_at=submitted_at + timedelta(days=3),            # Application 已有 Candidate、采用的 ResumeSubmission、冻结 ResumeProfile
            # 与冻结 JD/Profile 的外键；文件路径、来源和画像状态均由关联对象推导。
        )
        self.db.add(application)
        self.db.flush()
        self.record_stage(
            application,
            user,
            "route_candidate_to_job",
            "none",
            initial_status,
            (
                "专业匹配通过，已创建岗位申请并进入初步筛选。"
                if job_profile is not None
                else "专业匹配通过，已创建岗位申请，等待岗位能力画像完成。"
            ),
        )
        return application

    def _default_hr_assignee_id(self) -> str:
        """返回唯一的在职 HR 负责人；缺失时在创建申请前明确拒绝。"""
        row = self.db.query(User).filter(
            User.role == "hr",
            User.is_active.is_(True),
            User.deleted_at.is_(None),
        ).order_by(User.user_id.asc()).first()
        if row is None:
            raise BusinessError(
                "hr_assignee_missing",
                "系统未配置可用 HR，不能创建需要后续审核的岗位申请",
                status_code=409,
            )
        return row.user_id
    def record_stage(
        self,
        application: Application,
        user: User,
        action: str,
        from_status: str,
        to_status: str,
        note: str,
        *,
        effective_at: datetime | None = None,
        business_timezone: str = "Asia/Shanghai",
    ) -> None:
        self.db.add(
            StageHistory(
                stage_history_id=_id("SH"),
                application_id=application.application_id,
                actor_role=user.role,
                actor_name=user.display_name,
                action=action,
                from_status=from_status,
                to_status=to_status,
                note=note,
                effective_at=effective_at or _now(),
                business_timezone=business_timezone,
            )
        )
