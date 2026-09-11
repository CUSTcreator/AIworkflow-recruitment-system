from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.mutable import MutableDict, MutableList
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from backend.app.db.session import Base


def now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


JsonType = MutableDict.as_mutable(JSON().with_variant(JSONB, "postgresql"))
JsonListType = MutableList.as_mutable(JSON().with_variant(JSONB, "postgresql"))


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str | None] = mapped_column(String(64), unique=True, index=True, nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)
    display_name: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(64), index=True)
    role_definition_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("role_definitions.role_id"), nullable=True, index=True
    )
    department_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("departments.department_id"), nullable=True
    )
    business_scope: Mapped[str] = mapped_column(String(32), default="department", index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_system_admin: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 停用可恢复；逻辑删除用于退出业务选择器但保留历史操作人和审计关联。
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    deleted_by_user_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("users.user_id"), nullable=True, index=True
    )


class RoleDefinition(Base):
    __tablename__ = "role_definitions"

    role_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    business_scope: Mapped[str] = mapped_column(String(32), default="department")
    permissions: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class UserPermissionOverride(Base):
    __tablename__ = "user_permission_overrides"
    __table_args__ = (
        UniqueConstraint("user_id", "permission_code", name="uq_user_permission_override"),
    )

    permission_override_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.user_id"), index=True)
    permission_code: Mapped[str] = mapped_column(String(96), index=True)
    effect: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Department(Base):
    __tablename__ = "departments"

    department_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    deleted_by_user_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("users.user_id"), nullable=True, index=True
    )


class InterviewGuideTemplate(Base):
    __tablename__ = "interview_guide_templates"
    __table_args__ = (
        Index(
            "uq_interview_guide_templates_default_round",
            "round",
            unique=True,
            postgresql_where=text("is_default = true AND is_active = true"),
            sqlite_where=text("is_default = 1 AND is_active = 1"),
        ),
    )

    template_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    round: Mapped[str] = mapped_column(String(32), default="first", index=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    # 归档题单不可再被岗位选择，但历史申请仍通过冻结的版本号追溯题目。
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    archived_by_user_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("users.user_id"), nullable=True, index=True
    )
    created_by: Mapped[str] = mapped_column(String(64), ForeignKey("users.user_id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class InterviewGuideTemplateVersion(Base):
    __tablename__ = "interview_guide_template_versions"
    __table_args__ = (
        UniqueConstraint("template_id", "version", name="uq_interview_guide_template_version"),
        Index("ix_interview_guide_template_versions_status", "template_id", "status"),
    )

    template_version_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    template_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("interview_guide_templates.template_id"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="draft")
    source_document_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("source_documents.source_document_id"), nullable=True, index=True
    )
    # 题目列表受 TemplateQuestionSchema 约束；不保存泛化业务包。
    questions_json: Mapped[list[Any]] = mapped_column(JsonListType, default=list)
    created_by: Mapped[str] = mapped_column(String(64), ForeignKey("users.user_id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class SourceDocument(Base):
    __tablename__ = "source_documents"
    __table_args__ = (
        # SourceDocument 只保存不可变文件资产。一次导入是否复用由对应 Submission/Import 判断。
        Index("ix_source_documents_type_sha256", "document_type", "source_sha256"),
    )

    source_document_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_type: Mapped[str] = mapped_column(String(32), index=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(128), default="application/pdf")
    object_ref: Mapped[str] = mapped_column(String(512))
    source_sha256: Mapped[str] = mapped_column(String(64), index=True)
    parsed_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 文档块是解析后的稳定工件索引。岗位、简历都可使用这些字段，不能再写进元数据包。
    parser_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    document_blocks_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    document_blocks_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    document_blocks_schema_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    external_document_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    uploaded_by: Mapped[str] = mapped_column(String(64), ForeignKey("users.user_id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class JobDocumentImport(Base):
    """一次岗位文件导入的业务状态与自助恢复合同。

    SourceDocument 只回答“上传了哪个文件”；WorkflowRun 只回答“后台怎样执行”。
    本对象是岗位导入页面判断排队、待确认、失败和完成的唯一状态来源。
    """

    __tablename__ = "job_document_imports"

    job_document_import_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_document_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("source_documents.source_document_id"), unique=True, index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    review_kind: Mapped[str | None] = mapped_column(String(48), nullable=True, index=True)
    failure_kind: Mapped[str | None] = mapped_column(String(48), nullable=True, index=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    recovery_code: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    recovery_context_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    workflow_run_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("workflow_runs.workflow_run_id"), nullable=True, index=True
    )
    uploaded_by: Mapped[str] = mapped_column(String(64), ForeignKey("users.user_id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class JobDraft(Base):
    __tablename__ = "job_drafts"
    __table_args__ = (
        UniqueConstraint(
            "source_document_id",
            "sequence_no",
            name="uq_job_draft_document_sequence",
        ),
    )

    job_draft_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_document_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("source_documents.source_document_id"), index=True
    )
    sequence_no: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(128))
    headcount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    responsibilities: Mapped[list[str]] = mapped_column(JSON, default=list)
    qualifications: Mapped[list[str]] = mapped_column(JSON, default=list)
    major_requirement: Mapped[str | None] = mapped_column(String(500), nullable=True)
    education_requirement: Mapped[str | None] = mapped_column(String(255), nullable=True)
    department_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("departments.department_id"), nullable=True, index=True
    )
    source_department_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    department_match_status: Mapped[str] = mapped_column(
        String(32), default="matched"
    )
    source_text: Mapped[str] = mapped_column(Text)
    # 导入草稿生命周期：draft 可编辑，confirmed/skipped/deleted 均为终态；deleted 仅作审计保留。
    status: Mapped[str] = mapped_column(String(32), index=True, default="draft")
    confirmed_job_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("jobs.job_id"), nullable=True, index=True
    )
    duplicate_status: Mapped[str] = mapped_column(String(32), default="new_job", index=True)
    existing_job_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("jobs.job_id"), nullable=True, index=True
    )
    duplicate_group_id: Mapped[str | None] = mapped_column(String(96), nullable=True, index=True)
    resolution: Mapped[str | None] = mapped_column(String(24), nullable=True)
    # 岗位导入辅助信息有固定合同，禁止再向泛化数据包 混写字段来源或模型选择结果。
    preset_model_id: Mapped[str] = mapped_column(String(64), default="engineering_experience")
    preset_model_version: Mapped[str] = mapped_column(String(32), default="1.0")
    recommended_preset_model_id: Mapped[str] = mapped_column(String(64), default="engineering_experience")
    preset_model_selection_source: Mapped[str] = mapped_column(String(32), default="rule")
    major_requirement_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    major_requirement_source_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 每个可编辑字段的来源、待确认状态和人工覆盖标记，受 JobDraftFieldProvenance 合同约束。
    field_provenance_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    # 硬筛确认前的完整要求分类；其中 hardScreeningRules 供页面编辑，items 供画像复用。
    requirement_classification_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    department_auto_created: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class ResumeSubmission(Base):
    __tablename__ = "resume_submissions"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_resume_submission_idempotency_key"),
        CheckConstraint(
            "status IN ('queued', 'parsing', 'extracting', 'review_required', 'failed', 'completed')",
            name="ck_resume_submissions_status",
        ),
        CheckConstraint(
            "intake_mode IN ('initial', 'reparse', 'replacement', 'manual_correction')",
            name="ck_resume_submissions_intake_mode",
        ),
        CheckConstraint(
            "parse_strategy IN ('reuse_verified_parse', 'force_fresh_parse')",
            name="ck_resume_submissions_parse_strategy",
        ),
        Index(
            "uq_resume_submission_external_application",
            "external_application_id",
            unique=True,
            postgresql_where=text("external_application_id IS NOT NULL"),
            sqlite_where=text("external_application_id IS NOT NULL"),
        ),
        Index("ix_resume_submissions_job_status", "job_id", "status"),
        Index("ix_resume_submissions_application_status", "application_id", "status"),
    )

    resume_submission_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_document_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("source_documents.source_document_id"), index=True
    )
    # Legacy fields remain nullable only to read historical submissions. New intake
    # is Candidate-scoped and creates Applications after professional routing.
    job_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("jobs.job_id"), nullable=True, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    external_candidate_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True
    )
    external_application_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True
    )
    candidate_name_override: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), index=True, default="queued")
    # 人工处理和终态失败的原因是简历任务的正式契约，不能再只藏在 JSON 中。
    review_kind: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    failure_kind: Mapped[str | None] = mapped_column(String(48), nullable=True, index=True)
    # ``recovery_code`` 对应用户可执行的恢复方案；技术异常正文不参与页面动作判断。
    recovery_code: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    recovery_context_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    candidate_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("candidates.candidate_id"), nullable=True, index=True
    )
    intake_mode: Mapped[str] = mapped_column(String(32), default="initial", index=True)
    # Reparse must explicitly choose whether to reuse a verified immutable parse.
    parse_strategy: Mapped[str] = mapped_column(String(32), default="reuse_verified_parse")
    # A manual correction is immutable JSON in object storage, never a generic entity payload.
    manual_correction_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    manual_correction_sha256: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # 被新版任务替代时保留来源，便于审计与安全回退。
    supersedes_submission_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("resume_submissions.resume_submission_id"), nullable=True, index=True
    )
    # 本次解析/结构化产生的正式画像；成功前为空。
    output_resume_profile_id: Mapped[str | None] = mapped_column(
        String(96), ForeignKey("resume_profiles.resume_profile_id"), nullable=True, index=True
    )
    # 解析结果属于一次 Submission，而不是不可变的原始文件。
    parsed_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 以下具名 JSON 均有对应合同；禁止再向泛化数据包 写入解析、结构化或分发数据。
    parse_result_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    structure_result_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    structure_result_sha256: Mapped[str | None] = mapped_column(String(128), nullable=True)
    structure_schema_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    structure_review_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    # 结构化阶段提取的基础信息，与解析元数据和人工复核上下文分开保存。
    structure_metadata_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    # 候选人归属等非结构化人工处理上下文，与结构化草稿分开保存。
    review_context_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    routing_status: Mapped[str] = mapped_column(String(48), default="idle", index=True)
    routing_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    routing_result_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    routing_workflow_run_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("workflow_runs.workflow_run_id"), nullable=True, index=True
    )
    # 旧字段仅兼容已有记录，新代码使用 supersedes_submission_id。
    rebuild_from_submission_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    application_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("applications.application_id"), nullable=True, index=True
    )
    business_submitted_at: Mapped[datetime] = mapped_column(DateTime)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploaded_by: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.user_id"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class ApplicationDocumentLink(Base):
    __tablename__ = "application_workspace_documents"
    __table_args__ = (
        UniqueConstraint("application_id", "source_document_id", name="uq_application_workspace_document_source"),
        Index("ix_application_workspace_documents_active_order", "application_id", "is_active", "sort_order"),
    )

    document_link_id: Mapped[str] = mapped_column("workspace_document_id", String(64), primary_key=True)
    application_id: Mapped[str] = mapped_column(String(64), ForeignKey("applications.application_id"), index=True)
    source_document_id: Mapped[str] = mapped_column(String(64), ForeignKey("source_documents.source_document_id"), index=True)
    display_name: Mapped[str] = mapped_column(String(128))
    category: Mapped[str] = mapped_column(String(32), default="other", index=True)
    source_stage: Mapped[str] = mapped_column(String(32), default="manual_upload", index=True)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    uploaded_by: Mapped[str] = mapped_column(String(64), ForeignKey("users.user_id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class CandidateDocumentLink(Base):
    """候选人长期资料与文件本体的关联。

    简历仍由 ResumeSubmission 管理；此表只承载成绩单、证书、测评等候选人级资料。
    """

    __tablename__ = "candidate_workspace_documents"
    __table_args__ = (
        UniqueConstraint("candidate_id", "source_document_id", name="uq_candidate_workspace_document_source"),
        Index("ix_candidate_workspace_documents_active_order", "candidate_id", "is_active", "sort_order"),
    )

    document_link_id: Mapped[str] = mapped_column("workspace_document_id", String(64), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(String(64), ForeignKey("candidates.candidate_id"), index=True)
    source_document_id: Mapped[str] = mapped_column(String(64), ForeignKey("source_documents.source_document_id"), index=True)
    display_name: Mapped[str] = mapped_column(String(128))
    category: Mapped[str] = mapped_column(String(32), default="other", index=True)
    source_stage: Mapped[str] = mapped_column(String(32), default="manual_upload", index=True)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    uploaded_by: Mapped[str] = mapped_column(String(64), ForeignKey("users.user_id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class JobDocumentLink(Base):
    __tablename__ = "job_workspace_documents"
    __table_args__ = (
        UniqueConstraint("job_id", "source_document_id", name="uq_job_workspace_document_source"),
        Index("ix_job_workspace_documents_active_order", "job_id", "is_active", "sort_order"),
    )

    document_link_id: Mapped[str] = mapped_column("workspace_document_id", String(64), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(64), ForeignKey("jobs.job_id"), index=True)
    source_document_id: Mapped[str] = mapped_column(String(64), ForeignKey("source_documents.source_document_id"), index=True)
    display_name: Mapped[str] = mapped_column(String(128))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    uploaded_by: Mapped[str] = mapped_column(String(64), ForeignKey("users.user_id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class JobPosition(Base):
    __tablename__ = "job_positions"
    __table_args__ = (
        UniqueConstraint(
            "department_id",
            "normalized_title",
            name="uq_job_position_department_title",
        ),
    )

    position_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    department_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("departments.department_id"), index=True
    )
    title: Mapped[str] = mapped_column(String(128))
    normalized_title: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Job(Base):
    """A hiring requisition. ``job_id`` remains the public compatibility name."""

    __tablename__ = "jobs"
    __table_args__ = (
        Index(
            "uq_jobs_one_open_per_position",
            "position_id",
            unique=True,
            postgresql_where=text("status = 'open'"),
            sqlite_where=text("status = 'open'"),
        ),
    )

    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    position_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("job_positions.position_id"), nullable=False, index=True
    )
    external_job_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    identity_key: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    jd_content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(128))
    department_id: Mapped[str] = mapped_column(String(64), ForeignKey("departments.department_id"))
    status: Mapped[str] = mapped_column(String(32), default="open", index=True)
    headcount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hiring_manager_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("users.user_id"), nullable=True, index=True
    )
    department_recruiter_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("users.user_id"), nullable=True, index=True
    )
    common_interview_template_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("interview_guide_templates.template_id"), nullable=True, index=True
    )
    # Canonical current requisition fields. JobDraft is only the pre-confirmation
    # import artifact and must not be required to render or evaluate a Job.
    source_document_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("source_documents.source_document_id"), nullable=True, index=True
    )
    jd_text: Mapped[str] = mapped_column(Text)
    responsibilities: Mapped[list[str]] = mapped_column(JSON, default=list)
    qualifications: Mapped[list[str]] = mapped_column(JSON, default=list)
    education_requirement: Mapped[str | None] = mapped_column(String(255), nullable=True)
    major_requirement: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # 当前岗位默认能力模型；每次创建 JD 版本时会复制到该版本的冻结具名字段。
    preset_model_id: Mapped[str] = mapped_column(String(64), default="engineering_experience")
    preset_model_version: Mapped[str] = mapped_column(String(32), default="1.0")
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    deleted_by_user_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("users.user_id"), nullable=True, index=True
    )


class JobVersionRecord(Base):
    __tablename__ = "job_versions"
    __table_args__ = (
        UniqueConstraint("job_id", "source_sha256", name="uq_job_version_job_source"),
        UniqueConstraint("job_id", "version", name="uq_job_version_job_number"),
    )

    jd_version_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(64), ForeignKey("jobs.job_id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    source_sha256: Mapped[str] = mapped_column(String(64), index=True)
    source_text: Mapped[str] = mapped_column(Text)
    # 冻结内容仅描述这一版 JD 的业务事实，不能包含 Job 当前状态、审计信息或运行时结果。
    frozen_schema_version: Mapped[str] = mapped_column(String(64), default="job_version_input_v1")
    frozen_job_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    preset_model_id: Mapped[str] = mapped_column(String(64), default="engineering_experience")
    preset_model_version: Mapped[str] = mapped_column(String(32), default="1.0")
    job_capability_algorithm_version: Mapped[str] = mapped_column(String(64), default="job_capability_v1.0")
    # 岗位是否开放由 Job.status 管理；本字段只表达本版 JD 的“最新画像构建”状态。
    # 即使重新生成期间 status=processing，active_job_profile_id 指向的旧画像仍然
    # 可以供已经创建或新建的 Application 冻结使用。
    profile_status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    active_job_profile_id: Mapped[str | None] = mapped_column(
        String(96), ForeignKey("job_requirement_profiles.job_profile_id"), nullable=True, index=True
    )
    profile_workflow_run_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("workflow_runs.workflow_run_id"), nullable=True, index=True
    )
    profile_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    profile_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    profile_completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 岗位画像/就绪发布失败时保留用户可执行的恢复事实，避免页面按 profile_status 猜动作。
    recovery_code: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    recovery_context_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Candidate(Base):
    __tablename__ = "candidates"

    candidate_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(128))
    # Candidate 只表达档案是否有效；解析、分发等过程状态由当前 Submission/Workflow 表达。
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    current_resume_submission_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("resume_submissions.resume_submission_id"), nullable=True, index=True
    )
    current_resume_profile_id: Mapped[str | None] = mapped_column(
        String(96), ForeignKey("resume_profiles.resume_profile_id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class CandidateProfile(Base):
    __tablename__ = "candidate_profiles"

    candidate_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("candidates.candidate_id"), primary_key=True
    )
    current_title: Mapped[str] = mapped_column(String(255), default="")
    years_of_experience: Mapped[str] = mapped_column(String(64), default="")
    education: Mapped[str] = mapped_column(String(255), default="")
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    school: Mapped[str] = mapped_column(String(255), default="")
    major: Mapped[str] = mapped_column(String(255), default="")
    phone: Mapped[str] = mapped_column(String(64), default="")
    email: Mapped[str] = mapped_column(String(255), default="")
    highest_degree: Mapped[str] = mapped_column(String(64), default="")
    graduation_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)

class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (
        Index(
            "uq_applications_active_candidate_job",
            "candidate_id",
            "job_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL AND status NOT IN ('resume_replaced', 'offer_process', 'closed_rejected', 'closed_cancelled')"),
            sqlite_where=text("deleted_at IS NULL AND status NOT IN ('resume_replaced', 'offer_process', 'closed_rejected', 'closed_cancelled')"),
        ),
    )

    application_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    candidate_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("candidates.candidate_id"), nullable=True, index=True)
    job_id: Mapped[str] = mapped_column(String(64), ForeignKey("jobs.job_id"))
    # 正式画像必须精确绑定一版冻结 JD；V1 评分通过 Application.jd_version_id 查找它。
    jd_version_id: Mapped[str] = mapped_column(
        String(96), ForeignKey("job_versions.jd_version_id"), nullable=False, index=True
    )
    # 创建申请时若岗位画像已就绪即冻结；否则保持为空，等 JobProfileReady 事件在
    # 同一短事务中补齐后才允许进入硬筛/初筛。
    job_profile_id: Mapped[str | None] = mapped_column(
        String(96), ForeignKey("job_requirement_profiles.job_profile_id"), nullable=True, index=True
    )
    source_resume_profile_id: Mapped[str | None] = mapped_column(
        String(96), ForeignKey("resume_profiles.resume_profile_id"), nullable=True, index=True
    )
    adopted_resume_submission_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("resume_submissions.resume_submission_id"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(64), index=True)
    department_id: Mapped[str] = mapped_column(String(64), ForeignKey("departments.department_id"))
    current_owner: Mapped[str] = mapped_column(String(128))
    # 岗位确认后即可创建等待画像的申请；招聘人员配置属于后续组织配置，不应阻止
    # 简历分发，因此两个指派字段允许在 setup_pending 阶段为空。
    assigned_first_interviewer: Mapped[str | None] = mapped_column(String(64), ForeignKey("users.user_id"), nullable=True)
    assigned_hr: Mapped[str | None] = mapped_column(String(64), ForeignKey("users.user_id"), nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    deleted_by_user_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("users.user_id"), nullable=True, index=True
    )
    # 硬筛对申请的业务投影。其详情归属 HardScreeningResult，不再混写页面临时 JSON。
    hard_screening_policy_id: Mapped[str | None] = mapped_column(String(96), ForeignKey("hard_screening_policies.policy_id"), nullable=True, index=True)
    hard_screening_status: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    hard_screening_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    rejection_stage: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # 主状态描述招聘进度；恢复码只描述最近一次未完成处理对应的自助恢复方案。
    # 页面必须消费后端动作 DTO，不能直接按该字段值自行决定展示哪些按钮。
    recovery_code: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    recovery_context_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)


class Interview(Base):
    __tablename__ = "interviews"
    __table_args__ = (
        UniqueConstraint(
            "application_id",
            "interview_type",
            name="uq_interview_application_type",
        ),
    )

    interview_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    application_id: Mapped[str] = mapped_column(String(64), ForeignKey("applications.application_id"))
    interview_type: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(64), default="created")
    # 面试过程记录属于唯一的正式 Interview；字段受 InterviewProgressSchema 约束。
    guide_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    progress_raw_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    progress_question_responses_json: Mapped[list[Any]] = mapped_column(JsonListType, default=list)
    progress_version: Mapped[int] = mapped_column(Integer, default=0)
    progress_updated_by: Mapped[str | None] = mapped_column(String(64), ForeignKey("users.user_id"), nullable=True)
    progress_created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    progress_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class InterviewRecordRecord(Base):
    __tablename__ = "interview_records"

    record_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    application_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("applications.application_id"), index=True
    )
    interview_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("interviews.interview_id"), nullable=True
    )
    stage: Mapped[str] = mapped_column(String(32), index=True)
    record_type: Mapped[str] = mapped_column(String(32))
    question_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    # 面试题可以被标记为“未提问/未记录”，此时不存在原始回答而不是空字符串证据。
    # ``NULL`` 表示没有面评内容；实际有内容时才保存不可变原文。
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 以下字段属于一次不可变面评记录的具名事实。不得再把归属、原文或语义分段
    # 混放到泛化数据包；V2/V3 只能通过这些字段冻结本轮原始证据。
    answer_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    interviewer_judgement: Mapped[str | None] = mapped_column(Text, nullable=True)
    segments_json: Mapped[list[Any]] = mapped_column(JsonListType, default=list)
    source_input_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        Index(
            "uq_tasks_active_application_type",
            "application_id",
            "task_type",
            unique=True,
            postgresql_where=text("status IN ('pending', 'in_progress')"),
            sqlite_where=text("status IN ('pending', 'in_progress')"),
        ),
        Index("ix_tasks_assignee_status_due", "assignee_user_id", "status", "due_at"),
    )

    task_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    application_id: Mapped[str] = mapped_column(String(64), ForeignKey("applications.application_id"))
    task_type: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    assignee_user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.user_id"))
    assignee_role: Mapped[str] = mapped_column(String(64))
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class NotificationReadCursor(Base):
    __tablename__ = "notification_read_cursors"

    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.user_id"), primary_key=True
    )
    channel: Mapped[str] = mapped_column(String(32), primary_key=True)
    last_read_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"
    __table_args__ = (
        Index("ix_workflow_runs_queue", "status", "available_at"),
        Index("ix_workflow_runs_application_type", "application_id", "workflow_type", "started_at"),
        Index("ix_workflow_runs_application_status_updated", "application_id", "status", "updated_at"),
        Index(
            "uq_workflow_active_subject_type",
            "workflow_type",
            "subject_type",
            "subject_id",
            unique=True,
            postgresql_where=text(
                "status IN ('pending', 'running') AND subject_id IS NOT NULL"
            ),
            sqlite_where=text(
                "status IN ('pending', 'running') AND subject_id IS NOT NULL"
            ),
        ),
        Index(
            "uq_workflow_active_application_type",
            "workflow_type",
            "application_id",
            unique=True,
            postgresql_where=text(
                "status IN ('pending', 'running') "
                "AND subject_id IS NULL AND application_id IS NOT NULL"
            ),
            sqlite_where=text(
                "status IN ('pending', 'running') "
                "AND subject_id IS NULL AND application_id IS NOT NULL"
            ),
        ),
    )

    workflow_run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    application_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("applications.application_id"), nullable=True, index=True
    )
    subject_type: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    subject_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    workflow_type: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    triggered_by: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.user_id")
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    input_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    available_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    # 任务定义版本固定在创建时，部署新版本后旧任务仍按原步骤计划恢复。
    definition_version: Mapped[int] = mapped_column(Integer, default=1)


class WorkflowStepCheckpoint(Base):
    """Workflow 内单个可恢复步骤的当前执行账本。

    正式业务数据仍进入 ResumeProfile、AssessmentVersion 等领域表；本表只保存
    产物引用、外部幂等键及恢复所需的最小状态，不能承载原始简历或模型回包。
    """

    __tablename__ = "workflow_step_checkpoints"
    __table_args__ = (
        UniqueConstraint("workflow_run_id", "step_name", name="uq_workflow_step_checkpoint_run_step"),
        Index("ix_workflow_step_checkpoint_due", "status", "next_attempt_at"),
        Index("ix_workflow_step_checkpoint_run_order", "workflow_run_id", "step_order"),
        Index("ix_workflow_step_checkpoint_external_job", "external_job_id"),
        # 任务中心按状态和更新时间查询当前步骤，错误类别用于稳定的用户侧投影。
        Index("ix_workflow_step_checkpoint_status_updated", "status", "updated_at"),
        Index("ix_workflow_step_checkpoint_error_category", "last_error_category"),
    )

    checkpoint_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workflow_run_id: Mapped[str] = mapped_column(String(64), ForeignKey("workflow_runs.workflow_run_id"), index=True)
    step_name: Mapped[str] = mapped_column(String(96))
    step_order: Mapped[int] = mapped_column(Integer)
    definition_version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    input_hash: Mapped[str] = mapped_column(String(128))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    external_job_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    external_request_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    output_refs_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    # 创建检查点时冻结自动重试上限；后续代码配置变化不会改写历史任务语义。
    # 0 只用于迁移前的历史检查点，运行时回退到其冻结的 Workflow 定义。
    max_attempts_snapshot: Mapped[int] = mapped_column(Integer, default=0)
    # 异步任务的轮询上限与自动重试上限分别冻结，避免部署后改变历史任务语义。
    max_poll_attempts_snapshot: Mapped[int] = mapped_column(Integer, default=0)
    poll_count: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # code 供诊断，category 供跨层稳定展示；message 只保存受限长度的运维信息。
    last_error_category: Mapped[str | None] = mapped_column(String(48), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class WorkflowActivityCheckpoint(Base):
    """Step 内可独立恢复的外部活动账本。

    它只保存单次或单组外部计算的恢复元数据、可用性结论与产物引用，不保存简历原文、提示词或
    领域结论。WorkflowStepCheckpoint 仍是业务 Step 的唯一状态；本表只使 Step 在
    下一次运行时复用成功活动并补跑失败活动。
    """

    __tablename__ = "workflow_activity_checkpoints"
    __table_args__ = (
        UniqueConstraint(
            "workflow_run_id", "parent_step_name", "activity_key",
            name="uq_workflow_activity_checkpoint_run_parent_key",
        ),
        Index("ix_workflow_activity_checkpoint_due", "status", "next_attempt_at"),
        Index("ix_workflow_activity_checkpoint_parent", "workflow_run_id", "parent_step_name"),
    )

    activity_checkpoint_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workflow_run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("workflow_runs.workflow_run_id"), index=True
    )
    parent_step_name: Mapped[str] = mapped_column(String(96), index=True)
    activity_key: Mapped[str] = mapped_column(String(128))
    input_hash: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    # execution status 与业务可用性分离：成功执行的 Activity 也可能是 degraded 或
    # blocked。后者由读模型和父 Step 决定是否可继续，不能伪装成技术执行失败。
    outcome_kind: Mapped[str | None] = mapped_column(String(24), nullable=True, index=True)
    resolution_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    quality_summary_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts_snapshot: Mapped[int] = mapped_column(Integer, default=3)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    output_refs_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    last_error_category: Mapped[str | None] = mapped_column(String(48), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)

class WorkflowExecutionEvent(Base):
    """面向任务时间线的不可变执行事件。

    它不是技术日志的镜像：只记录可安全展示的工作流/步骤状态变化、稳定错误码和
    关联 ID。原始异常、简历文本、提示词与供应商回包仍只允许出现在受脱敏保护的
    运行日志或正式业务产物中。
    """

    __tablename__ = "workflow_execution_events"
    __table_args__ = (
        Index("ix_workflow_execution_events_run_time", "workflow_run_id", "occurred_at"),
        Index("ix_workflow_execution_events_application_time", "application_id", "occurred_at"),
        Index("ix_workflow_execution_events_candidate_time", "candidate_id", "occurred_at"),
    )

    execution_event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workflow_run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("workflow_runs.workflow_run_id"), index=True
    )
    application_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    candidate_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    resume_submission_id: Mapped[str | None] = mapped_column(String(96), nullable=True, index=True)
    step_name: Mapped[str | None] = mapped_column(String(96), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(96), index=True)
    severity: Mapped[str] = mapped_column(String(16), default="info", index=True)
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    external_request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    attempt_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    poll_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_category: Mapped[str | None] = mapped_column(String(48), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    public_message: Mapped[str] = mapped_column(String(500))
    diagnostic_fields: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)


class WorkflowArtifact(Base):
    __tablename__ = "workflow_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "workflow_run_id",
            "artifact_type",
            "version",
            name="uq_workflow_artifact_run_type_version",
        ),
    )

    artifact_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workflow_run_id: Mapped[str] = mapped_column(String(64), ForeignKey("workflow_runs.workflow_run_id"), index=True)
    # Artifact 可归属岗位版本、简历提交、Candidate 或 Application；application 不是强制入口。
    subject_type: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    subject_id: Mapped[str | None] = mapped_column(String(96), nullable=True, index=True)
    application_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("applications.application_id"), nullable=True, index=True)
    artifact_type: Mapped[str] = mapped_column(String(80), index=True)
    artifact_json: Mapped[dict[str, Any] | None] = mapped_column(JsonType, nullable=True)
    object_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    object_sha256: Mapped[str | None] = mapped_column(String(128), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class ScoreSnapshot(Base):
    __tablename__ = "score_snapshots"
    __table_args__ = (
        UniqueConstraint("application_id", "version", name="uq_score_application_version"),
    )

    score_snapshot_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    application_id: Mapped[str] = mapped_column(String(64), ForeignKey("applications.application_id"), index=True)
    stage: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(Integer)
    previous_snapshot_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("score_snapshots.score_snapshot_id"), nullable=True)

    capability_profile_id: Mapped[str | None] = mapped_column(String(96), ForeignKey("candidate_capability_profiles.profile_id"), nullable=True)
    score: Mapped[int] = mapped_column(Integer)
    # 历史评分结果只保存 AssessmentSnapshotSchema 规定的数据。
    result_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class ResumeProfileRecord(Base):
    __tablename__ = "resume_profiles"
    __table_args__ = (
        UniqueConstraint(
            "candidate_id", "version",
            name="uq_resume_profile_candidate_version",
        ),
        UniqueConstraint(
            "candidate_id", "source_sha256",
            name="uq_resume_profile_candidate_source",
        ),
    )

    resume_profile_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(String(64), ForeignKey("candidates.candidate_id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    # 同一冻结 JD 可因模型升级或人工重新生成产生多份不可变画像结果；revision=1
    # 为首次生成。Application 始终冻结具体 job_profile_id，不会被后续 revision 覆盖。
    revision: Mapped[int] = mapped_column(Integer, default=1)
    source_sha256: Mapped[str] = mapped_column(String(128), index=True)
    # 完整结构化简历是有版本的领域事实，使用具名 JSON 和 ResumeProfileSchema 校验。
    profile_schema_version: Mapped[str] = mapped_column(String(64), default="resume_profile_v1_1")
    profile_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    @property
    def profile_data(self) -> dict[str, Any]:
        """正式画像的唯一来源；禁止回退到已移除的泛化字段。"""
        return dict(self.profile_json or {})


class JobRequirementProfileRecord(Base):
    __tablename__ = "job_requirement_profiles"
    __table_args__ = (
        UniqueConstraint("jd_version_id", "revision", name="uq_job_requirement_profile_version_revision"),
    )

    job_profile_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(64), ForeignKey("jobs.job_id"), index=True)
    # 正式画像必须精确绑定一版冻结 JD；V1 评分通过 Application.jd_version_id 查找它。
    jd_version_id: Mapped[str] = mapped_column(
        String(96), ForeignKey("job_versions.jd_version_id"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    # 同一冻结 JD 可因模型升级或人工重新生成产生多份不可变画像结果；revision=1
    # 为首次生成。Application 始终冻结具体 job_profile_id，不会被后续 revision 覆盖。
    revision: Mapped[int] = mapped_column(Integer, default=1)
    source_sha256: Mapped[str] = mapped_column(String(128), index=True)
    # 正式岗位画像是版本化算法输出；运行轨迹和原始 JD 不得放入该 JSON。
    profile_schema_version: Mapped[str] = mapped_column(String(64), default="job_requirement_profile_v1")
    algorithm_version: Mapped[str] = mapped_column(String(64), default="job_capability_v1.0")
    profile_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class HardScreeningCriterion(Base):
    __tablename__ = "hard_screening_criteria"
    __table_args__ = (
        UniqueConstraint("code", name="uq_hard_screening_criterion_code"),
        Index("ix_hard_screening_criteria_enabled_order", "enabled", "sort_order"),
    )

    criterion_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    code: Mapped[str] = mapped_column(String(96), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    value_mode: Mapped[str] = mapped_column(String(16))
    allowed_values_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    evaluation_binding: Mapped[str] = mapped_column(String(96))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)

class HardScreeningPolicy(Base):
    __tablename__ = "hard_screening_policies"
    __table_args__ = (
        UniqueConstraint("job_id", "version", name="uq_hard_screening_policy_job_version"),
    )

    policy_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(64), ForeignKey("jobs.job_id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    rules_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    created_by: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.user_id")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class HardScreeningResult(Base):
    __tablename__ = "hard_screening_results"
    __table_args__ = (
        UniqueConstraint(
            "application_id",
            "policy_id",
            name="uq_hard_screening_result_application_policy",
        ),
    )

    result_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    application_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("applications.application_id"), index=True
    )
    policy_id: Mapped[str] = mapped_column(
        String(96), ForeignKey("hard_screening_policies.policy_id"), index=True
    )
    workflow_run_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("workflow_runs.workflow_run_id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    rule_results_json: Mapped[list[Any]] = mapped_column(JsonListType, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class UniversityRankingEntry(Base):
    __tablename__ = "university_ranking_entries"
    __table_args__ = (
        UniqueConstraint("dataset_version", "canonical_name", name="uq_university_ranking_dataset_name"),
        Index("ix_university_ranking_dataset_rank", "dataset_version", "rank"),
    )

    entry_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    dataset_version: Mapped[str] = mapped_column(String(64), index=True)
    ranking_source: Mapped[str] = mapped_column(String(128))
    ranking_year: Mapped[int] = mapped_column(Integer)
    canonical_name: Mapped[str] = mapped_column(String(128), index=True)
    aliases: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    rank: Mapped[int] = mapped_column(Integer)
    school_score: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class InterviewParseResultRecord(Base):
    """正式发布的面评语义结果。"""
    __tablename__ = "interview_parse_results"
    __table_args__ = (UniqueConstraint("application_id", "stage", "version", name="uq_interview_parse_application_stage_version"),)

    parse_result_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    application_id: Mapped[str] = mapped_column(String(64), ForeignKey("applications.application_id"), index=True)
    interview_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("interviews.interview_id"), nullable=True)
    stage: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(Integer)
    parse_schema_version: Mapped[str] = mapped_column(String(64), default="interview_parse_result_v2")
    source_record_ids: Mapped[list[Any]] = mapped_column(JsonListType, default=list)
    segments_json: Mapped[list[Any]] = mapped_column(JsonListType, default=list)
    assertions_json: Mapped[list[Any]] = mapped_column(JsonListType, default=list)
    parser_version: Mapped[str] = mapped_column(String(64), default="")
    source_hash: Mapped[str] = mapped_column(String(128), default="")
    no_new_evidence: Mapped[bool] = mapped_column(Boolean, default=False)
    resolved_interview_target_ids: Mapped[list[Any]] = mapped_column(JsonListType, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)



class CandidateCapabilityProfileRecord(Base):
    __tablename__ = "candidate_capability_profiles"
    __table_args__ = (
        UniqueConstraint("application_id", "stage", "version", name="uq_capability_profile_application_stage_version"),
        Index("ix_capability_profile_interview_parse", "source_interview_parse_result_id"),
    )

    profile_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    application_id: Mapped[str] = mapped_column(String(64), ForeignKey("applications.application_id"), index=True)
    stage: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(Integer)
    previous_profile_id: Mapped[str | None] = mapped_column(String(96), ForeignKey("candidate_capability_profiles.profile_id"), nullable=True)

    resume_profile_id: Mapped[str] = mapped_column(String(96), ForeignKey("resume_profiles.resume_profile_id"))
    job_profile_id: Mapped[str] = mapped_column(String(96), ForeignKey("job_requirement_profiles.job_profile_id"))
    # 初筛为空；面后版本直接指向本次面评解析结果，形成可追溯的明确来源。
    source_interview_parse_result_id: Mapped[str | None] = mapped_column(
        String(96), ForeignKey("interview_parse_results.parse_result_id"), nullable=True
    )
    # 评分核心能力画像受 CapabilityProfileSchema 约束，展示结果归属 AssessmentVersion。
    capability_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

class ApplicationAssessmentVersion(Base):
    """一次申请在某个评估阶段发布的唯一正式结果。

    source_json 记录冻结来源；core_result_json、rule_result_json、presentation_json 分别保存评分、规则和展示层。
    这四段均有代码合同，不能混入原始面试笔记或泛化页面 payload。
    """

    __tablename__ = "application_assessment_versions"
    __table_args__ = (
        UniqueConstraint(
            "application_id", "stage", "version",
            name="uq_assessment_version_application_stage_version",
        ),
        Index("ix_assessment_versions_application_created", "application_id", "created_at"),
        Index("ix_assessment_version_interview_parse", "source_interview_parse_result_id"),
        CheckConstraint("stage IN ('screening', 'after_first_interview', 'after_second_interview')", name="ck_assessment_version_stage"),
    )

    assessment_version_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    application_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("applications.application_id"), index=True
    )
    stage: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(Integer)
    previous_assessment_version_id: Mapped[str | None] = mapped_column(
        String(96), ForeignKey("application_assessment_versions.assessment_version_id"), nullable=True
    )
    resume_profile_id: Mapped[str] = mapped_column(
        String(96), ForeignKey("resume_profiles.resume_profile_id"), index=True
    )
    job_profile_id: Mapped[str] = mapped_column(
        String(96), ForeignKey("job_requirement_profiles.job_profile_id"), index=True
    )
    source_interview_parse_result_id: Mapped[str | None] = mapped_column(
        String(96), ForeignKey("interview_parse_results.parse_result_id"), nullable=True
    )
    # 命名 JSON 区块均有对应 Pydantic/运行时 Schema，不使用泛化数据包 混放结果。
    source_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    core_result_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    rule_result_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    presentation_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AssessmentTopologyDefinition(Base):
    """一份初筛 V1 固化的能力评分拓扑定义。

    定义层只回答“有哪些可更新锚点、它们如何聚合、各锚点对应哪条业务事实”，
    不保存任一轮的分数。每个 ApplicationAssessmentVersion V1 恰有一份定义；
    V2、V3 只能复用该定义，不能在面评后临时增加节点、边或新的证据配对。
    """

    __tablename__ = "assessment_topology_definitions"
    __table_args__ = (
        UniqueConstraint("root_assessment_version_id", name="uq_assessment_topology_definition_root_version"),
        Index("ix_assessment_topology_definition_application", "application_id"),
        Index("ix_assessment_topology_definition_structure_hash", "structure_hash"),
    )

    topology_definition_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    # 必须指向初筛 V1；该约束由发布服务校验，数据库保留通用评估版本外键。
    root_assessment_version_id: Mapped[str] = mapped_column(
        String(96), ForeignKey("application_assessment_versions.assessment_version_id"), index=True
    )
    application_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("applications.application_id"), index=True
    )
    structure_schema_version: Mapped[str] = mapped_column(
        String(64), default="assessment_topology_definition_v1"
    )
    # 仅由节点、边和引用计算；分数变化不得改变该哈希。
    structure_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class AssessmentTopologyNode(Base):
    """拓扑中的一个稳定锚点，如 JDUnit、JDCapability、WU×指标或 Project PAO。

    ``anchor_key`` 是同一拓扑内稳定且可重复计算的业务键；V2/V3 通过它定位
    已冻结节点。节点自身不存分数，避免把不同评估版本的状态写到同一行。
    """

    __tablename__ = "assessment_topology_nodes"
    __table_args__ = (
        UniqueConstraint("topology_definition_id", "anchor_key", name="uq_assessment_topology_node_key"),
        Index("ix_assessment_topology_node_definition_type", "topology_definition_id", "anchor_type"),
    )

    topology_node_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    topology_definition_id: Mapped[str] = mapped_column(
        String(96), ForeignKey("assessment_topology_definitions.topology_definition_id"), index=True
    )
    anchor_key: Mapped[str] = mapped_column(String(192))
    anchor_type: Mapped[str] = mapped_column(String(64), index=True)
    # 仅用于同类型节点的稳定展示与算法遍历，不表达父子关系。
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class AssessmentTopologyEdge(Base):
    """定义层的有向聚合边：子节点的变更应沿该边向父节点聚合。"""

    __tablename__ = "assessment_topology_edges"
    __table_args__ = (
        UniqueConstraint(
            "topology_definition_id", "parent_node_id", "child_node_id", "relation_type",
            name="uq_assessment_topology_edge",
        ),
        CheckConstraint("parent_node_id <> child_node_id", name="ck_assessment_topology_edge_not_self"),
        Index("ix_assessment_topology_edge_child", "child_node_id"),
    )

    topology_edge_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    topology_definition_id: Mapped[str] = mapped_column(
        String(96), ForeignKey("assessment_topology_definitions.topology_definition_id"), index=True
    )
    parent_node_id: Mapped[str] = mapped_column(
        String(96), ForeignKey("assessment_topology_nodes.topology_node_id"), index=True
    )
    child_node_id: Mapped[str] = mapped_column(
        String(96), ForeignKey("assessment_topology_nodes.topology_node_id"), index=True
    )
    relation_type: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class AssessmentTopologyNodeReference(Base):
    """节点与简历/岗位画像中事实的规范化引用。

    画像内部事实当前是版本化 JSON 文档，未必都有独立 SQL 主键，因此以
    ``reference_kind + reference_id`` 保存其稳定业务标识，而不是把整个事实复制
    到拓扑表。读取时由对应 Profile 版本按该标识解析并校验。
    """

    __tablename__ = "assessment_topology_node_references"
    __table_args__ = (
        UniqueConstraint(
            "topology_node_id", "reference_role", "reference_kind", "reference_id",
            name="uq_assessment_topology_node_reference",
        ),
        Index("ix_assessment_topology_node_reference_lookup", "reference_kind", "reference_id"),
    )

    topology_node_reference_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    topology_node_id: Mapped[str] = mapped_column(
        String(96), ForeignKey("assessment_topology_nodes.topology_node_id"), index=True
    )
    reference_role: Mapped[str] = mapped_column(String(64))
    reference_kind: Mapped[str] = mapped_column(String(64))
    reference_id: Mapped[str] = mapped_column(String(192))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class AssessmentTopologySnapshot(Base):
    """一个正式评估版本对既有拓扑的一次不可变节点状态快照。

    V1 建立首份状态；V2 指向 V1 快照，V3 指向 V2 快照。结构始终由
    :class:`AssessmentTopologyDefinition` 提供，状态只写入
    :class:`AssessmentTopologyNodeState`，从而保证面评增量不能重写评分拓扑。

    ``topology_json`` 是早期开发阶段的过渡字段，后续迁移完成后不再作为业务
    读取入口；它保留到新表稳定后再经单独确认删除。
    """

    __tablename__ = "assessment_topology_snapshots"
    __table_args__ = (
        UniqueConstraint("assessment_version_id", name="uq_assessment_topology_snapshot_version"),
        Index("ix_assessment_topology_snapshot_application", "application_id"),
        Index("ix_assessment_topology_snapshot_definition", "topology_definition_id"),
    )

    topology_snapshot_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    assessment_version_id: Mapped[str] = mapped_column(
        String(96), ForeignKey("application_assessment_versions.assessment_version_id"), index=True
    )
    application_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("applications.application_id"), index=True
    )
    # 迁移落地前允许为空，正式写路径完成后必须由发布服务写入。
    topology_definition_id: Mapped[str | None] = mapped_column(
        String(96), ForeignKey("assessment_topology_definitions.topology_definition_id"), nullable=True, index=True
    )
    previous_topology_snapshot_id: Mapped[str | None] = mapped_column(
        String(96), ForeignKey("assessment_topology_snapshots.topology_snapshot_id"), nullable=True, index=True
    )
    state_schema_version: Mapped[str] = mapped_column(
        String(64), default="assessment_topology_state_v1"
    )
    source_core_hash: Mapped[str] = mapped_column(String(64))
    # 由本快照的节点状态计算，用于检测状态写入是否被意外篡改。
    state_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # 以下三项仅服务于 JSON 过渡期；新逻辑不得据此读取结构或状态。
    schema_version: Mapped[str] = mapped_column(String(64), default="assessment_topology_snapshot_v1")
    topology_hash: Mapped[str] = mapped_column(String(64), index=True)
    topology_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class AssessmentTopologyNodeState(Base):
    """某份 Snapshot 中一个已冻结节点的分数与可用状态。

    每个 ``(snapshot, node)`` 仅一行。V2/V3 发布时复制上一版状态，再仅更新被
    面评命中的节点及其聚合祖先；不能插入新节点，也不能改变定义层的关系。
    """

    __tablename__ = "assessment_topology_node_states"
    __table_args__ = (
        UniqueConstraint("topology_snapshot_id", "topology_node_id", name="uq_assessment_topology_node_state"),
        Index("ix_assessment_topology_state_snapshot_score", "topology_snapshot_id", "score_value"),
    )

    topology_node_state_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    topology_snapshot_id: Mapped[str] = mapped_column(
        String(96), ForeignKey("assessment_topology_snapshots.topology_snapshot_id"), index=True
    )
    topology_node_id: Mapped[str] = mapped_column(
        String(96), ForeignKey("assessment_topology_nodes.topology_node_id"), index=True
    )
    score_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    level_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    state: Mapped[str] = mapped_column(String(32), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class AssessmentAnchorUpdate(Base):
    """面评断言对一个锚点造成的已发布增量，供审计和前端变化说明查询。

    原始面评全文仍归 InterviewParseResultRecord 管理；这里仅存断言标识、必要
    摘录和规则判断，避免把原始 LLM 返回、页面文案或整份面评复制进该表。
    """

    __tablename__ = "assessment_anchor_updates"
    __table_args__ = (
        Index("ix_assessment_anchor_update_snapshot_node", "topology_snapshot_id", "topology_node_id"),
        Index("ix_assessment_anchor_update_parse_result", "source_interview_parse_result_id"),
    )

    assessment_anchor_update_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    topology_snapshot_id: Mapped[str] = mapped_column(
        String(96), ForeignKey("assessment_topology_snapshots.topology_snapshot_id"), index=True
    )
    topology_node_id: Mapped[str] = mapped_column(
        String(96), ForeignKey("assessment_topology_nodes.topology_node_id"), index=True
    )
    source_interview_parse_result_id: Mapped[str | None] = mapped_column(
        String(96), ForeignKey("interview_parse_results.parse_result_id"), nullable=True
    )
    assertion_key: Mapped[str] = mapped_column(String(128), index=True)
    judgement: Mapped[str] = mapped_column(String(32))
    before_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    after_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 仅保存支撑本次判断的最小摘录；完整原文通过 parse result 追溯。
    source_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

class FirstInterviewPlanVersion(Base):
    """一次一面题单规划发布的正式版本。

    题单生成与正式 ``InterviewGuide`` 分离：本对象保存规划阶段的可追溯结果；
    面试官确认后，才由确认命令物化为正式题单和逐题记录。
    """

    __tablename__ = "first_interview_plan_versions"
    __table_args__ = (
        UniqueConstraint(
            "application_id", "version",
            name="uq_first_interview_plan_application_version",
        ),
        Index("ix_first_interview_plan_application_created", "application_id", "created_at"),
        Index("ix_first_interview_plan_source_assessment", "source_assessment_version_id"),
    )

    plan_version_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    application_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("applications.application_id"), index=True
    )
    source_assessment_version_id: Mapped[str] = mapped_column(
        String(96), ForeignKey("application_assessment_versions.assessment_version_id"), index=True
    )
    previous_plan_version_id: Mapped[str | None] = mapped_column(
        String(96), ForeignKey("first_interview_plan_versions.plan_version_id"), nullable=True
    )
    template_version_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    # 与初筛版本一致，四段均有明确语义，不使用泛化数据包。
    source_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    core_result_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    rule_result_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    presentation_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class StageHistory(Base):
    __tablename__ = "stage_history"

    stage_history_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    application_id: Mapped[str] = mapped_column(String(64), ForeignKey("applications.application_id"), index=True)
    actor_role: Mapped[str] = mapped_column(String(64))
    actor_name: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(80))
    from_status: Mapped[str] = mapped_column(String(64))
    to_status: Mapped[str] = mapped_column(String(64))
    note: Mapped[str] = mapped_column(Text)
    effective_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    business_timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class HumanDecision(Base):
    __tablename__ = "human_decisions"

    decision_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    application_id: Mapped[str] = mapped_column(String(64), ForeignKey("applications.application_id"), index=True)
    actor_role: Mapped[str] = mapped_column(String(64))
    actor_name: Mapped[str] = mapped_column(String(128))
    decision: Mapped[str] = mapped_column(String(80))
    from_status: Mapped[str] = mapped_column(String(64))
    to_status: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text)
    effective_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    business_timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_target", "target_type", "target_id"),
        Index("ix_audit_events_created_at", "created_at"),        Index("ix_audit_events_request", "request_id", "created_at"),
        Index("ix_audit_events_workflow", "workflow_run_id", "created_at"),
    )

    audit_event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    actor_user_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("users.user_id"), nullable=True, index=True
    )
    actor_name: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(96), index=True)
    target_type: Mapped[str] = mapped_column(String(64), index=True)
    target_id: Mapped[str] = mapped_column(String(96), index=True)
    summary: Mapped[str] = mapped_column(String(500))
    details: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)    # 与 HTTP/异步任务关联；用于审计检索，不改变既有业务审计语义。
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    workflow_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (UniqueConstraint("user_id", "idempotency_key", name="uq_user_idempotency_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.user_id"), index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default="completed")
    response_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class ScreeningAssessment(Base):
    """初筛阶段的发布结果。

    分数、硬筛结论和页面版本等稳定字段单独建列；复杂且版本化的页面视图仍以
    JSON 保存，但不再使用无约束的通用 ``泛化数据包`` 表。
    """

    __tablename__ = "screening_assessments"
    __table_args__ = (
        UniqueConstraint("application_id", "version", name="uq_screening_assessment_application_version"),
        Index("ix_screening_assessments_application_created", "application_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    screening_assessment_id: Mapped[str] = mapped_column(String(96), unique=True, index=True)
    application_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("applications.application_id"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    score_status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    base_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    qualification_gate: Mapped[str] = mapped_column(String(32), default="unclear")
    job_capability_fit_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    resume_demonstrated_capability_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    resume_experience_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    education_background_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    source_bundle_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source_snapshot_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    generation_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    screening_result_view: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    decision_overview: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    failure_details: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class InterviewTarget(Base):
    """按申请持久化的跨阶段面试核验目标。

    status 只允许 open/resolved；优势、薄弱项及其新增/保持/关闭比较属于 AAV.rule_result_json，不存本表。
    """

    __tablename__ = "interview_targets"
    __table_args__ = (
        UniqueConstraint("application_id", "interview_target_id", name="uq_interview_target_application_target"),
        Index("ix_interview_targets_application_status", "application_id", "status"),
        CheckConstraint("status IN ('open', 'resolved')", name="ck_interview_target_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    application_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("applications.application_id"), index=True
    )
    interview_target_id: Mapped[str] = mapped_column(String(128), index=True)
    purpose: Mapped[str] = mapped_column(String(64), default="verify_experience")
    target_type: Mapped[str] = mapped_column(String(64), default="job_capability")
    target_id: Mapped[str] = mapped_column(String(128), default="")
    title: Mapped[str] = mapped_column(String(300), default="")
    verification_goal: Mapped[str] = mapped_column(Text, default="")
    trigger_code: Mapped[str] = mapped_column(String(96), default="")
    status: Mapped[str] = mapped_column(String(32), default="open", index=True)
    stage_created: Mapped[str] = mapped_column(String(64), default="screening")
    resolved_stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_result_ids: Mapped[list[Any]] = mapped_column(JsonListType, default=list)
    evidence_ids: Mapped[list[Any]] = mapped_column(JsonListType, default=list)
    attributes: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class InterviewGuide(Base):
    """面试官确认后物化的正式一面题单。

    ``plan_version_id`` 和 ``guide_id`` 将执行态题单精确绑定到规划版本；同一
    Application 后续重新生成题单时，V2 不会混入旧 Guide 或旧 Question。
    历史行允许为空，供迁移期间保留审计；新确认流程必须写入两个关联字段。
    """

    __tablename__ = "interview_guides"
    __table_args__ = (
        UniqueConstraint("application_id", "guide_id", name="uq_interview_guide_application_guide"),
        Index("ix_interview_guides_plan_version", "plan_version_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    application_id: Mapped[str] = mapped_column(String(64), ForeignKey("applications.application_id"), index=True)
    guide_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    plan_version_id: Mapped[str | None] = mapped_column(
        String(96), ForeignKey("first_interview_plan_versions.plan_version_id"), nullable=True
    )
    # 已确认题纲的整体执行内容；身份和版本关系由具名列保存。
    content_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class InterviewQuestion(Base):
    """正式一面逐题执行记录，必须从属一个确认后的 Guide 和 PlanVersion。"""

    __tablename__ = "interview_questions"
    __table_args__ = (
        Index("ix_interview_questions_plan_guide", "application_id", "plan_version_id", "guide_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    application_id: Mapped[str] = mapped_column(String(64), ForeignKey("applications.application_id"), index=True)
    guide_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    plan_version_id: Mapped[str | None] = mapped_column(
        String(96), ForeignKey("first_interview_plan_versions.plan_version_id"), nullable=True
    )
    # 已确认逐题执行内容（题干、追问、Rubric、Target 绑定与顺序）。
    question_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
