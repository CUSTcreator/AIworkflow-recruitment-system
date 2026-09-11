"""Document ingestion 的读取响应 Schema。"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from backend.app.modules.candidates.public import ResumeSubmissionStatus
from backend.app.shared.recovery_actions import RecoveryActionView


class JobDocumentUploadResponse(BaseModel):
    document_id: str
    import_id: str
    workflow_run_id: str
    import_status: str
    reused: bool = False



class ResumeSubmissionView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    resume_submission_id: str
    source_document_id: str
    job_id: str | None
    status: ResumeSubmissionStatus
    candidate_id: str | None
    application_id: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime



class ResumeParsedContentView(BaseModel):
    submission_id: str
    filename: str
    submission_status: ResumeSubmissionStatus
    source_available: bool
    parsed_text: str
    parse_result: dict = Field(default_factory=dict)
    error_message: str | None


class JobOptionView(BaseModel):
    job_id: str
    title: str
    department_id: str
    department_name: str
    status: str


class JobDocumentImportView(BaseModel):
    import_id: str
    source_document_id: str
    document_type: str
    original_filename: str
    import_status: str
    error_message: str | None
    recovery_code: str | None = None
    recovery_context_json: dict = Field(default_factory=dict)
    available_actions: list[RecoveryActionView] = Field(default_factory=list)
    # SourceDocument 只描述文件及解析工件；结构化结果归 ResumeSubmission/JobDraft，不能混入通用 JSON。
    parser_provider: str | None
    document_blocks_ref: str | None
    document_blocks_sha256: str | None
    document_blocks_schema_version: str | None
    created_at: datetime
    updated_at: datetime


class JobDraftFieldHint(BaseModel):
    field: Literal[
        "title",
        "headcount",
        "responsibilities",
        "qualifications",
        "education_requirement",
        "major_requirement",
        "department_id",
    ]
    status: Literal["ai_assisted", "review_required"]
    message: str


class JobDraftExtractionAssistance(BaseModel):
    status: Literal["ai_assisted", "review_required"]
    field_hints: list[JobDraftFieldHint] = Field(default_factory=list)


class JobDraftHardScreeningPreview(BaseModel):
    """导入确认页展示并允许用户修改的硬筛条件。"""

    criterion_type: str
    name: str
    expected_value: str | float
    description: str = ""
    enabled: bool = True


class JobDraftView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    job_draft_id: str
    source_document_id: str
    sequence_no: int
    title: str
    headcount: int | None
    responsibilities: list[str]
    qualifications: list[str]
    education_requirement: str | None
    major_requirement: str | None
    department_id: str | None
    source_department_name: str | None
    department_match_status: str
    source_text: str
    status: str
    confirmed_job_id: str | None
    duplicate_status: str
    existing_job_id: str | None
    duplicate_group_id: str | None
    resolution: str | None
    preset_model_id: str
    preset_model_version: str
    recommended_preset_model_id: str
    updated_at: datetime
    extraction_assistance: JobDraftExtractionAssistance | None = None
    hard_screening_preview: list[JobDraftHardScreeningPreview] = Field(default_factory=list)
    available_actions: list[RecoveryActionView] = Field(default_factory=list)


class DeleteJobDraftResponse(BaseModel):
    """删除未确认岗位草稿后的最小响应。"""

    document_id: str
    draft_id: str
    draft_status: str
    import_status: str

class ConfirmedJobView(BaseModel):
    """岗位确认接口的发布结果。

    jd_version_id 指向刚冻结的 JD；profile_status/profile_workflow_run_id 让前端能
    明确显示“岗位已确认，但能力画像仍在生成”的异步状态。
    """

    job_id: str | None
    job_draft_id: str
    title: str
    reused: bool
    skipped: bool = False
    jd_version_id: str | None = None
    profile_status: str | None = None
    profile_workflow_run_id: str | None = None


class ConfirmJobDraftsResponse(BaseModel):
    document_id: str
    import_status: str
    jobs: list[ConfirmedJobView]
