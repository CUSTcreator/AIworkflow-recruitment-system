from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


FactKind = Literal[
    "other_text",
    "experience_title",
    "skill_claim",
    "practice_context",
    "action_bullet",
    "outcome_bullet",
    "education_entry",
    "credential_entry",
    "no_evidence",
]


@dataclass(slots=True)
class CandidateSpan:
    span_id: str
    section: str
    rough_type: str
    experience_unit_id: str | None
    source_line_start: int
    source_line_end: int
    subspan_index: int
    text: str
    source_block_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SourceBullet:
    source_bullet_id: str
    experience_unit_id: str
    raw_text: str
    source_line_start: int
    source_line_end: int
    work_unit_ids: list[str]
    source_block_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ScorableWorkUnit:
    work_unit_id: str
    project_id: str
    # WorkUnit 由项目级模型选择 SourceBullet 分组，正文和位置始终由后端依据
    # source_refs 回填；这些派生字段不允许模型自行生成。
    raw_text: str = ""
    source_line_start: int | None = None
    source_line_end: int | None = None
    source_block_ids: list[str] = field(default_factory=list)
    source_refs: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class ExperienceUnit:
    experience_unit_id: str
    title: str
    span_ids: list[str]
    source_bullet_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ProjectContextItem:
    context_id: str
    experience_unit_id: str
    context_type: Literal[
        "project_title",
        "project_date",
        "project_description",
        "tech_stack",
        "development_environment",
        "other_context",
    ]
    text: str
    source_line_start: int
    source_line_end: int
    source_block_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ResumeInputQualityReport:
    status: Literal["pass", "warning", "reject"]
    character_count: int
    nonempty_line_count: int
    section_headers_found: list[str]
    project_count: int
    scorable_source_count: int
    suspicious_long_line_ratio: float
    duplicate_line_ratio: float
    garbled_character_ratio: float
    unassigned_text_ratio: float
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class VerifiedResumeIR:
    resume_ir_version: str
    candidate_id: str
    resume_raw_sha256: str
    resume_redacted_sha256: str
    candidate_spans: list[CandidateSpan]
    experience_units: list[ExperienceUnit]
    # One source of truth for education and hard-screening facts.
    candidate_facts: dict[str, Any] = field(default_factory=dict)
    source_bullets: list[SourceBullet] = field(default_factory=list)
    scorable_work_units: list[ScorableWorkUnit] = field(default_factory=list)
    skill_statements: list[dict[str, Any]] = field(default_factory=list)
    redaction_warnings: list[str] = field(default_factory=list)
    project_context_items: list[ProjectContextItem] = field(default_factory=list)
    input_quality_report: ResumeInputQualityReport | None = None
    structuring_provenance: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ScreeningScoringInput:
    application_id: str
    candidate_id: str
    job_id: str
    jd_text: str
    # 评分读取已经由结构化流程发布并冻结的简历画像，不再读取原始文件。
    resume_profile: dict[str, Any] = field(default_factory=dict)
    # 仅为兼容历史调用保留；新工作流不得依赖该字段重建 ResumeProfile。
    resume_structure: dict[str, Any] = field(default_factory=dict)
    llm_config: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

