"""V2/V3 共用增量评分管线的纯输入输出合同。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


JsonMapping = Mapping[str, Any]


def _mappings(values: Sequence[JsonMapping]) -> tuple[JsonMapping, ...]:
    return tuple(dict(item) for item in values if isinstance(item, Mapping))


@dataclass(frozen=True, slots=True)
class IncrementalEvidence:
    """根据冻结事实与已发布 IPR 版本链临时构建的评分证据。

    此对象只存在于 Workflow Artifact / 内存：它不创建新数据库 ID，也不复制
    ResumeProfile。``prior_interview_parse_result_ids`` 使 V3 可明确继承 V2 面评已
    产生的变化，而不是只依赖上一版 AAV 的间接引用。
    """

    affected_result_refs: Sequence[str]
    unchanged_result_refs: Sequence[str]
    evidence_index: Mapping[str, JsonMapping]
    scoring_evidence: Mapping[str, Any] = field(default_factory=dict)
    prior_interview_parse_result_ids: Sequence[str] = field(default_factory=tuple)
    current_interview_record_ids: Sequence[str] = field(default_factory=tuple)
    assertions: Sequence[JsonMapping] = field(default_factory=tuple)
    # 新版锚点评估活动的原始结果。这里只保存普通 JSON；评分管线会在内部
    # 将它们整理为 ObservationTargetResult，不要求 LLM 返回数据库字段。
    anchor_judgements: Sequence[JsonMapping] = field(default_factory=tuple)
    # 面评解析器明确确认、可关闭的既有 InterviewTarget 稳定 ID。
    resolved_interview_target_ids: Sequence[str] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "affected_result_refs": list(self.affected_result_refs),
            "unchanged_result_refs": list(self.unchanged_result_refs),
            "evidence_index": {key: dict(value) for key, value in self.evidence_index.items()},
            "scoring_evidence": dict(self.scoring_evidence),
            "prior_interview_parse_result_ids": list(self.prior_interview_parse_result_ids),
            "current_interview_record_ids": list(self.current_interview_record_ids),
            "assertions": [dict(item) for item in self.assertions],
            "anchor_judgements": [dict(item) for item in self.anchor_judgements],
            "resolved_interview_target_ids": list(self.resolved_interview_target_ids),
        }

    @classmethod
    def from_dict(cls, value: JsonMapping) -> "IncrementalEvidence":
        raw_index = value.get("evidence_index")
        return cls(
            affected_result_refs=tuple(str(item) for item in value.get("affected_result_refs") or ()),
            unchanged_result_refs=tuple(str(item) for item in value.get("unchanged_result_refs") or ()),
            evidence_index={
                str(key): dict(item)
                for key, item in (raw_index.items() if isinstance(raw_index, Mapping) else ())
                if isinstance(item, Mapping)
            },
            scoring_evidence=dict(value.get("scoring_evidence") or {}) if isinstance(value.get("scoring_evidence"), Mapping) else {},
            prior_interview_parse_result_ids=tuple(
                str(item) for item in value.get("prior_interview_parse_result_ids") or ()
            ),
            current_interview_record_ids=tuple(
                str(item) for item in value.get("current_interview_record_ids") or ()
            ),
            assertions=_mappings(value.get("assertions") or ()),
            anchor_judgements=_mappings(value.get("anchor_judgements") or ()),
            resolved_interview_target_ids=tuple(
                str(item) for item in value.get("resolved_interview_target_ids") or () if str(item)
            ),
        )


@dataclass(frozen=True, slots=True)
class IncrementalEvidenceInput:
    """构建临时评分证据的输入；不含 Application/Candidate 身份字段。"""

    resume_profile: JsonMapping
    previous_core_result: JsonMapping
    prior_interview_parse_results: Sequence[JsonMapping]
    current_interview_record_ids: Sequence[str]
    interview_parse_draft: JsonMapping


@dataclass(frozen=True, slots=True)
class IncrementalScoringInput:
    """纯增量评分唯一输入。

    ``previous_core_result`` 始终是经过 ``AssessmentCoreResult`` 归一化的 V1 或 V2
    基线；算法仅重算 ``evidence`` 标记的受影响范围。
    """

    resume_profile: JsonMapping
    job_requirement_profile: JsonMapping
    previous_core_result: JsonMapping
    evidence: IncrementalEvidence


@dataclass(frozen=True, slots=True)
class AffectedRecomputationPlan:
    """增量评分必须重算与允许沿用的完整范围。

    范围对应核心流程文档：WU → Project → 预设指标/能力框架 → JDCapability →
    JDUnit → 两项能力分与总分；NonScoring 不产生任何受影响项。
    """

    affected_result_refs: Sequence[str]
    unchanged_result_refs: Sequence[str]
    recompute_work_unit_ids: Sequence[str] = field(default_factory=tuple)
    recompute_project_ids: Sequence[str] = field(default_factory=tuple)
    recompute_preset_indicator_ids: Sequence[str] = field(default_factory=tuple)
    recompute_capability_framework_ids: Sequence[str] = field(default_factory=tuple)
    recompute_job_capability_ids: Sequence[str] = field(default_factory=tuple)
    recompute_job_unit_ids: Sequence[str] = field(default_factory=tuple)
    recompute_score_dimensions: Sequence[str] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "affected_result_refs": list(self.affected_result_refs),
            "unchanged_result_refs": list(self.unchanged_result_refs),
            "recompute_work_unit_ids": list(self.recompute_work_unit_ids),
            "recompute_project_ids": list(self.recompute_project_ids),
            "recompute_preset_indicator_ids": list(self.recompute_preset_indicator_ids),
            "recompute_capability_framework_ids": list(self.recompute_capability_framework_ids),
            "recompute_job_capability_ids": list(self.recompute_job_capability_ids),
            "recompute_job_unit_ids": list(self.recompute_job_unit_ids),
            "recompute_score_dimensions": list(self.recompute_score_dimensions),
        }

    @classmethod
    def from_dict(cls, value: JsonMapping) -> "AffectedRecomputationPlan":
        def strings(name: str) -> tuple[str, ...]:
            return tuple(str(item) for item in value.get(name) or ())

        return cls(
            affected_result_refs=strings("affected_result_refs"),
            unchanged_result_refs=strings("unchanged_result_refs"),
            recompute_work_unit_ids=strings("recompute_work_unit_ids"),
            recompute_project_ids=strings("recompute_project_ids"),
            recompute_preset_indicator_ids=strings("recompute_preset_indicator_ids"),
            recompute_capability_framework_ids=strings("recompute_capability_framework_ids"),
            recompute_job_capability_ids=strings("recompute_job_capability_ids"),
            recompute_job_unit_ids=strings("recompute_job_unit_ids"),
            recompute_score_dimensions=strings("recompute_score_dimensions"),
        )


@dataclass(frozen=True, slots=True)
class IncrementalScoringResult:
    """V2/V3 的原始评分结果。

    字段刻意与 ``AssessmentCoreResult`` 一致；发布器只需进行 Schema 校验，不能再
    为 V2/V3 发明另一套 ``preset_experience_result`` 或分数字段名称。
    """

    experience_result: JsonMapping
    job_result: JsonMapping
    education_result: JsonMapping
    score_result: JsonMapping
    capability_graph: JsonMapping
    affected_result_refs: Sequence[str] = field(default_factory=tuple)
    score_changes: Sequence[JsonMapping] = field(default_factory=tuple)
    capability_changes: Sequence[JsonMapping] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "experience_result": dict(self.experience_result),
            "job_result": dict(self.job_result),
            "education_result": dict(self.education_result),
            "score_result": dict(self.score_result),
            "capability_graph": dict(self.capability_graph),
            "affected_result_refs": list(self.affected_result_refs),
            "score_changes": [dict(item) for item in self.score_changes],
            "capability_changes": [dict(item) for item in self.capability_changes],
        }

    @classmethod
    def from_dict(cls, value: JsonMapping) -> "IncrementalScoringResult":
        def mapping(name: str) -> JsonMapping:
            raw = value.get(name)
            return dict(raw) if isinstance(raw, Mapping) else {}

        return cls(
            experience_result=mapping("experience_result"),
            job_result=mapping("job_result"),
            education_result=mapping("education_result"),
            score_result=mapping("score_result"),
            capability_graph=mapping("capability_graph"),
            affected_result_refs=tuple(str(item) for item in value.get("affected_result_refs") or ()),
            score_changes=_mappings(value.get("score_changes") or ()),
            capability_changes=_mappings(value.get("capability_changes") or ()),
        )
