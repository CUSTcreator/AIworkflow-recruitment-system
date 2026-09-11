"""岗位管理的流程决策器。

该服务与 CandidateIntakeProcessService 的职责一致：HTTP 命令和 Job 文档 Workflow
发布步骤都通过它解释业务动作；它只调用状态机并修改已加载对象，不提交事务、不做
外部调用。现有 document_ingestion Service 将在后续迁移中逐步接入这里。
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from backend.app.modules.jobs.domain.job_draft_state_machine import (
    JobDraftAction,
    transition_job_draft,
)
from backend.app.modules.jobs.domain.job_state_machine import JobAction, transition_job


class JobProfileStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    READY = "ready"
    REVIEW_REQUIRED = "review_required"
    FAILED = "failed"


class JobProcessService:
    """JobDraft、Job 与 JobVersion 画像状态的唯一业务解释入口。"""

    @staticmethod
    def require_draft_review(draft, *, now: datetime) -> None:
        transition_job_draft(draft, JobDraftAction.REQUIRE_REVIEW)
        draft.updated_at = now

    @staticmethod
    def confirm_draft(draft, *, job_id: str, now: datetime) -> None:
        transition_job_draft(draft, JobDraftAction.CONFIRM)
        draft.confirmed_job_id = job_id
        draft.updated_at = now

    @staticmethod
    def discard_draft(draft, *, now: datetime) -> None:
        transition_job_draft(draft, JobDraftAction.DISCARD)
        draft.updated_at = now

    @staticmethod
    def skip_draft(draft, *, now: datetime) -> None:
        transition_job_draft(draft, JobDraftAction.SKIP)
        draft.updated_at = now

    @staticmethod
    def delete_draft(draft, *, now: datetime) -> None:
        transition_job_draft(draft, JobDraftAction.DELETE)
        draft.updated_at = now

    @staticmethod
    def publish_job(job, *, now: datetime) -> None:
        transition_job(job, JobAction.PUBLISH)
        job.opened_at = now
        job.closed_at = None

    @staticmethod
    def close_job(job, *, now: datetime) -> None:
        transition_job(job, JobAction.CLOSE)
        job.closed_at = now

    @staticmethod
    def require_setup(job, *, now: datetime) -> None:
        """撤销开放岗位的必要人员配置，回到待完成配置而不是关闭岗位。"""
        transition_job(job, JobAction.REQUIRE_SETUP)
        job.closed_at = None

    @staticmethod
    def reopen_job(job, *, now: datetime) -> None:
        transition_job(job, JobAction.REOPEN)
        job.opened_at = now
        job.closed_at = None

    @staticmethod
    def mark_profile(job_version, *, status: JobProfileStatus | str, now: datetime, reason: str | None = None) -> None:
        """记录不可变岗位版本画像的运行状态，并拒绝倒退或越级覆盖。"""
        status_value = str(status)
        if status_value not in {item.value for item in JobProfileStatus}:
            raise ValueError(f"未知岗位画像状态：{status_value}")
        current = str(getattr(job_version, "profile_status", None) or JobProfileStatus.QUEUED.value)
        allowed = {
            JobProfileStatus.QUEUED.value: {"queued", "processing", "ready", "review_required", "failed"},
            JobProfileStatus.PROCESSING.value: {"processing", "queued", "ready", "review_required", "failed"},
            JobProfileStatus.REVIEW_REQUIRED.value: {"review_required", "queued", "ready"},
            JobProfileStatus.FAILED.value: {"failed", "queued", "ready"},
            # 已发布版本是终态。重新生成必须创建新的运行并保留旧画像指针，
            # 不能把已就绪版本回退为排队、处理中或失败。
            JobProfileStatus.READY.value: {"ready"},
        }
        if status_value not in allowed[current]:
            raise ValueError(f"岗位画像状态不允许从 {current} 变为 {status_value}")
        job_version.profile_status = status_value
        job_version.profile_error_message = reason if status_value in {JobProfileStatus.FAILED.value, JobProfileStatus.REVIEW_REQUIRED.value} else None
        if status_value == JobProfileStatus.QUEUED.value:
            job_version.profile_started_at = None
            job_version.profile_completed_at = None
        elif status_value == JobProfileStatus.PROCESSING.value:
            job_version.profile_started_at = job_version.profile_started_at or now
            job_version.profile_completed_at = None
        elif status_value in {JobProfileStatus.READY.value, JobProfileStatus.REVIEW_REQUIRED.value, JobProfileStatus.FAILED.value}:
            job_version.profile_completed_at = now