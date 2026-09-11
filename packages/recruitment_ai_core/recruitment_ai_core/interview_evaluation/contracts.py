"""V2/V3 面评语义解析管线的纯输入输出合同。

输入只包含已经冻结的事实版本；算法包不导入 Application、Candidate、ORM、数据库
会话或 HTTP DTO。字段使用 snake_case，后端可直接将最终解析草稿写入 IPR.payload。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


JsonMapping = Mapping[str, Any]


def _mappings(values: Sequence[JsonMapping]) -> tuple[JsonMapping, ...]:
    return tuple(dict(value) for value in values if isinstance(value, Mapping))


@dataclass(frozen=True, slots=True)
class InterviewParseInput:
    """面评解析的冻结输入与候选目标全集。

    - 逐题记录只能使用 ``confirmed_questions`` 中同一 ``question_id`` 的预绑定
      Target、能力叶子、scenario 和 ``evaluation_rubrics``；
    - 自由记录按类别遍历 ``work_units``、``skill_claims``、``job_capabilities`` 和
      ``preset_indicator_definitions`` 的完整有效集合；
    - 原始 ResumeProfile / JobRequirementProfile 同时保留，供派生结果校验和溯源。
    """

    stage: str
    interview_records: Sequence[JsonMapping]
    resume_profile: JsonMapping
    job_requirement_profile: JsonMapping
    open_targets: Sequence[JsonMapping]
    confirmed_questions: Sequence[JsonMapping] = field(default_factory=tuple)
    work_units: Sequence[JsonMapping] = field(default_factory=tuple)
    skill_claims: Sequence[JsonMapping] = field(default_factory=tuple)
    job_capabilities: Sequence[JsonMapping] = field(default_factory=tuple)
    preset_indicator_definitions: Sequence[JsonMapping] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "interview_records": [dict(item) for item in self.interview_records],
            "resume_profile": dict(self.resume_profile),
            "job_requirement_profile": dict(self.job_requirement_profile),
            "open_targets": [dict(item) for item in self.open_targets],
            "confirmed_questions": [dict(item) for item in self.confirmed_questions],
            "work_units": [dict(item) for item in self.work_units],
            "skill_claims": [dict(item) for item in self.skill_claims],
            "job_capabilities": [dict(item) for item in self.job_capabilities],
            "preset_indicator_definitions": [dict(item) for item in self.preset_indicator_definitions],
        }

    @classmethod
    def from_dict(cls, value: JsonMapping) -> "InterviewParseInput":
        return cls(
            stage=str(value["stage"]),
            interview_records=_mappings(value.get("interview_records") or ()),
            resume_profile=dict(value.get("resume_profile") or {}),
            job_requirement_profile=dict(value.get("job_requirement_profile") or {}),
            open_targets=_mappings(value.get("open_targets") or ()),
            confirmed_questions=_mappings(value.get("confirmed_questions") or ()),
            work_units=_mappings(value.get("work_units") or ()),
            skill_claims=_mappings(value.get("skill_claims") or ()),
            job_capabilities=_mappings(value.get("job_capabilities") or ()),
            preset_indicator_definitions=_mappings(
                value.get("preset_indicator_definitions") or ()
            ),
        )


@dataclass(frozen=True, slots=True)
class InterviewSegment:
    """来自不可变面评记录的一段可追溯语义片段及其处理状态。"""

    segment_id: str
    record_id: str
    raw_text: str
    category: str
    source_refs: Sequence[JsonMapping]
    resolution_status: str = "needs_review"
    review_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "segment_id": self.segment_id,
            "record_id": self.record_id,
            "raw_text": self.raw_text,
            "category": self.category,
            "source_refs": [dict(item) for item in self.source_refs],
            "resolution_status": self.resolution_status,
            "review_reason": self.review_reason,
        }

    @classmethod
    def from_dict(cls, value: JsonMapping) -> "InterviewSegment":
        return cls(
            segment_id=str(value["segment_id"]),
            record_id=str(value["record_id"]),
            raw_text=str(value["raw_text"]),
            category=str(value["category"]),
            source_refs=_mappings(value.get("source_refs") or ()),
            resolution_status=str(value.get("resolution_status") or "needs_review"),
            review_reason=(str(value["review_reason"]) if value.get("review_reason") else None),
        )


@dataclass(frozen=True, slots=True)
class InterviewParseDraft:
    """完整但尚未发布的面评解析草稿。

    ``assertions`` 是唯一的解析结果入口；每条断言同时携带原文引用、记录类型和
    冻结拓扑候选锚点。其它阶段不得再按语义类型拆分平行列表。
    """

    parser_version: str
    source_record_ids: Sequence[str]
    segments: Sequence[InterviewSegment]
    assertions: Sequence[JsonMapping]
    source_hash: str = ""
    review_required: bool = False
    # 全部记录均无文本时为真；允许发布 IPR/AAV，只是不产生增量评分证据。
    no_new_evidence: bool = False
    # 仅含本轮已取得充分直接证据、允许关闭的既有 Open Target 稳定 ID。
    resolved_interview_target_ids: Sequence[str] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "parser_version": self.parser_version,
            "source_record_ids": list(self.source_record_ids),
            "segments": [item.as_dict() for item in self.segments],
            "assertions": [dict(item) for item in self.assertions],
            "source_hash": self.source_hash,
            "review_required": self.review_required,
            "no_new_evidence": self.no_new_evidence,
            "resolved_interview_target_ids": list(self.resolved_interview_target_ids),
        }

    @classmethod
    def from_dict(cls, value: JsonMapping) -> "InterviewParseDraft":
        return cls(
            parser_version=str(value["parser_version"]),
            source_record_ids=tuple(str(item) for item in value.get("source_record_ids") or ()),
            segments=tuple(InterviewSegment.from_dict(item) for item in value.get("segments") or ()),
            assertions=_mappings(value.get("assertions") or ()),
            source_hash=str(value.get("source_hash") or ""),
            review_required=bool(value.get("review_required")),
            no_new_evidence=bool(value.get("no_new_evidence")),
            resolved_interview_target_ids=tuple(
                str(item) for item in value.get("resolved_interview_target_ids") or () if str(item)
            ),
        )


@dataclass(frozen=True, slots=True)
class ParseValidationIssue:
    """解析草稿的一项可读校验问题。"""

    code: str
    message: str
    segment_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "segment_id": self.segment_id}

    @classmethod
    def from_dict(cls, value: JsonMapping) -> "ParseValidationIssue":
        return cls(
            code=str(value["code"]),
            message=str(value["message"]),
            segment_id=(str(value["segment_id"]) if value.get("segment_id") else None),
        )


@dataclass(frozen=True, slots=True)
class ParseValidationResult:
    """统一断言校验结果。"""

    draft: InterviewParseDraft
    issues: Sequence[ParseValidationIssue]
    needs_repair: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "draft": self.draft.as_dict(),
            "issues": [item.as_dict() for item in self.issues],
            "needs_repair": self.needs_repair,
        }

    @classmethod
    def from_dict(cls, value: JsonMapping) -> "ParseValidationResult":
        return cls(
            draft=InterviewParseDraft.from_dict(dict(value["draft"])),
            issues=tuple(ParseValidationIssue.from_dict(item) for item in value.get("issues") or ()),
            needs_repair=bool(value["needs_repair"]),
        )
# 面评证据的统一合同。语义属性和原文溯源都在同一条断言中保存。
EVIDENCE_DISPOSITIONS = {"scored", "non_scoring", "review_required"}


@dataclass(frozen=True, slots=True)
class InterviewEvidenceItem:
    """一次提取出的、可追溯的面评语义单元。"""

    evidence_id: str
    record_id: str
    source_refs: Sequence[JsonMapping]
    disposition: str
    # 绑定锚点、判断方向及分数留给后续的锚点评估步骤。
    text: str = ""
    is_capability_evaluation: bool = False
    is_experience_related: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "record_id": self.record_id,
            "source_refs": [dict(item) for item in self.source_refs],
            "disposition": self.disposition,
            "text": self.text,
            "is_capability_evaluation": self.is_capability_evaluation,
            "is_experience_related": self.is_experience_related,
        }

    @classmethod
    def from_dict(cls, value: JsonMapping) -> "InterviewEvidenceItem":
        return cls(
            evidence_id=str(value["evidence_id"]),
            record_id=str(value["record_id"]),
            source_refs=_mappings(value.get("source_refs") or ()),
            disposition=str(value["disposition"]),
            text=str(value.get("text") or ""),
            is_capability_evaluation=bool(value.get("is_capability_evaluation")),
            is_experience_related=bool(value.get("is_experience_related")),
        )


@dataclass(frozen=True, slots=True)
class InterviewEvidenceExtractionResult:
    """“提取并绑定面评证据”步骤的可恢复产物。

    该结果不含 ORM、Application 或分数；Workflow 只持久化为 Artifact。后续本地
    规范化器根据冻结候选目标白名单决定哪些 Item 能真正进入 ``scored`` 分支。
    """

    parser_version: str
    source_record_ids: Sequence[str]
    items: Sequence[InterviewEvidenceItem]
    no_new_evidence: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "parser_version": self.parser_version,
            "source_record_ids": list(self.source_record_ids),
            "items": [item.as_dict() for item in self.items],
            "no_new_evidence": self.no_new_evidence,
        }

    @classmethod
    def from_dict(cls, value: JsonMapping) -> "InterviewEvidenceExtractionResult":
        return cls(
            parser_version=str(value["parser_version"]),
            source_record_ids=tuple(str(item) for item in value.get("source_record_ids") or ()),
            items=tuple(InterviewEvidenceItem.from_dict(item) for item in value.get("items") or ()),
            no_new_evidence=bool(value.get("no_new_evidence")),
        )
