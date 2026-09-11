from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field
from backend.app.shared.recovery_actions import RecoveryActionView


class JobProfileRunRequest(BaseModel):
    """岗位画像重试/重新生成命令。reason 用于审计，不参与算法输入。"""

    reason: str | None = Field(default=None, max_length=500)


class JobProfileRunView(BaseModel):
    job_id: str
    jd_version_id: str
    workflow_run_id: str
    profile_status: Literal["queued", "processing", "ready", "review_required", "failed"]
    action: Literal["retry", "regenerate"]


class JobManagementView(BaseModel):
    """岗位管理页的 Job 聚合 DTO。

    Job 是主行；当前 JobVersion、其当前有效画像及其 Workflow 只是该主行的运行投影。
    """

    job_id: str
    title: str
    department_id: str
    department_name: str
    headcount: int | None
    status: str
    hiring_manager_name: str | None
    department_recruiter_name: str | None
    job_configuration_status: Literal["complete", "incomplete"] = "complete"
    missing_job_assignments: list[
        Literal["hiring_manager", "department_recruiter"]
    ] = Field(default_factory=list)
    candidate_count: int = 0
    source_document_id: str | None
    source_filename: str | None
    source_type: Literal["file_import", "untracked"]
    jd_text: str
    responsibilities: list[str] = Field(default_factory=list)
    qualifications: list[str] = Field(default_factory=list)
    education_requirement: str | None
    major_requirement: str | None = None
    common_interview_template_id: str | None = None
    common_interview_template_name: str | None = None
    inherits_default_interview_template: bool = True
    opened_at: datetime
    closed_at: datetime | None

    # 当前冻结 JD 与岗位能力画像：供岗位列表展示“待配置/生成中/失败/可用于评分”。
    jd_version_id: str | None = None
    jd_version: int | None = None
    profile_status: Literal["queued", "processing", "ready", "review_required", "failed", "not_started"] = "not_started"
    active_job_profile_id: str | None = None
    profile_workflow_run_id: str | None = None
    profile_error_message: str | None = None
    profile_degraded: bool = False
    profile_quality_message: str | None = None
    recovery_code: str | None = None
    recovery_context: dict = Field(default_factory=dict)
    can_route_candidate: bool = False
    can_start_screening: bool = False
    waiting_application_count: int = 0
    available_actions: list[RecoveryActionView] = Field(default_factory=list)


class ImportRecordView(BaseModel):
    """导入记录摘要，以及当前用户可直接执行的恢复动作。"""

    import_id: str
    import_type: Literal["job", "resume"]
    filename: str
    display_name: str
    status: str
    result_summary: str
    job_id: str | None = None
    job_title: str | None = None
    application_id: str | None = None
    target_ids: list[str] = Field(default_factory=list)
    screening_status: str | None = None
    error_message: str | None = None
    process: dict | None = None
    available_actions: list[RecoveryActionView] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class ImportRecordListView(BaseModel):
    items: list[ImportRecordView]
    total: int
    unread_action_count: int = 0


class ImportRecordReadView(BaseModel):
    import_type: Literal["job", "resume"]
    last_read_at: datetime


class NavigationNotificationView(BaseModel):
    task_unread_count: int = 0
    candidate_unread_count: int = 0


class NavigationNotificationReadView(BaseModel):
    channel: Literal["task", "candidate_application"]
    last_read_at: datetime


class JobUpdateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=128)
    headcount: int | None = Field(default=None, ge=1, le=100_000)
    jd_text: str = Field(min_length=1, max_length=50_000)
    responsibilities: list[str] = Field(default_factory=list, max_length=100)
    qualifications: list[str] = Field(default_factory=list, max_length=100)
    education_requirement: str | None = Field(default=None, max_length=255)
    major_requirement: str | None = Field(default=None, max_length=500)
