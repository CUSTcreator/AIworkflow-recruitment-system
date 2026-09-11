"""为冻结评分拓扑补充面评所需的只读语义。

拓扑的节点和边仍只来自 V1 已发布 Definition。本模块只按节点已经保存的稳定引用，
从同一次冻结的 ResumeProfile、JobRequirementProfile 和上一版核心结果中查回短文本，
不得发现新节点、建立新关系或读取展示文案。
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from recruitment_ai_core.screening_scoring.resume_experience.preset_models import (
    get_preset_model,
)


_EXPERIENCE_TYPES = {
    "work_unit_indicator",
    "project_pao",
    "project_indicator",
    "project_framework",
    "candidate_framework",
}
_JOB_TYPES = {"skill_claim", "job_capability_evidence_pair", "job_capability", "job_unit"}
_SEMANTIC_FIELDS = (
    "capability_name",
    "capability_definition",
    "skill_name",
    "name",
    "title",
    "definition",
    "ideal_definition",
    "activation_boundary",
    "raw_text",
    "text",
    "statement",
    "quote",
    "details",
    "dimension",
)


def enrich_frozen_topology(
    topology: Mapping[str, Any],
    *,
    resume_profile: Mapping[str, Any],
    job_profile: Mapping[str, Any],
    previous_core_result: Mapping[str, Any],
) -> dict[str, Any]:
    """按现有 Reference 为每个节点装配 description、region 和可评估标记。"""
    index = _semantic_index(resume_profile, job_profile, previous_core_result)
    enriched = dict(topology)
    nodes: list[dict[str, Any]] = []
    for raw in topology.get("nodes") or ():
        if not isinstance(raw, Mapping):
            continue
        node = dict(raw)
        anchor_type = _text(node.get("anchorType") or node.get("anchor_type"))
        refs = [
            dict(item)
            for item in node.get("references") or ()
            if isinstance(item, Mapping)
        ]
        parts: list[str] = []
        for reference in refs:
            kind = _text(reference.get("kind") or reference.get("reference_kind"))
            identifier = _text(reference.get("id") or reference.get("reference_id"))
            candidates = index.get((kind, identifier), ()) or index.get(("*", identifier), ())
            for item in _preferred_reference_rows(kind, identifier, candidates):
                parts.extend(_semantic_parts(item))
        node["description"] = _join_unique(parts)
        node["region"] = _region(anchor_type, refs)
        # Pair 表示 V1 中“旧证据 -> 能力”的历史关联。新面评不能冒充旧证据，
        # 只能直接评价能力、岗位单元或经历侧锚点。
        node["interviewEligible"] = anchor_type != "job_capability_evidence_pair"
        nodes.append(node)
    enriched["nodes"] = nodes
    return enriched


def _semantic_index(*sources: Mapping[str, Any]) -> dict[tuple[str, str], tuple[Mapping[str, Any], ...]]:
    values: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            row = dict(value)
            for field, raw_id in row.items():
                if not str(field).endswith("_id"):
                    continue
                identifier = _text(raw_id)
                if not identifier:
                    continue
                kind = str(field).removesuffix("_id")
                values[(kind, identifier)].append(row)
                values[("*", identifier)].append(row)
                if kind == "experience_unit":
                    values[("project", identifier)].append(row)
                elif kind == "jd_unit":
                    values[("job_unit", identifier)].append(row)
            for child in value.values():
                visit(child)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            for child in value:
                visit(child)

    for source in sources:
        visit(source)
    try:
        model = get_preset_model(
            _text(sources[1].get("preset_model_id")) or None,
            _text(sources[1].get("preset_model_version")) or None,
        )
    except (IndexError, ValueError):
        model = {}
    visit(model)
    return {key: tuple(items) for key, items in values.items()}


def _semantic_parts(value: Mapping[str, Any]) -> list[str]:
    parts: list[str] = []
    for field in _SEMANTIC_FIELDS:
        raw = value.get(field)
        if isinstance(raw, str):
            if text := raw.strip():
                parts.append(text)
        elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
            parts.extend(_text(item) for item in raw if _text(item))
    return parts


def _preferred_reference_rows(
    kind: str,
    identifier: str,
    candidates: Sequence[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], ...]:
    """优先取引用对象自身，避免父节点把所有子节点描述一起拼入 Prompt。"""
    preferred: list[Mapping[str, Any]] = []
    for item in candidates:
        if kind == "project":
            matches = _text(item.get("experience_unit_id")) == identifier
        elif kind == "job_unit":
            matches = (
                _text(item.get("job_unit_id") or item.get("jd_unit_id")) == identifier
                and not _text(item.get("job_capability_id"))
            )
        elif kind == "framework":
            matches = (
                _text(item.get("framework_id")) == identifier
                and not _text(item.get("indicator_id"))
                and not _text(item.get("project_id"))
            )
        else:
            matches = _text(item.get(f"{kind}_id")) == identifier
        if matches and _semantic_parts(item):
            preferred.append(item)
    if preferred:
        return tuple(preferred)
    return tuple(item for item in candidates if _semantic_parts(item))


def _join_unique(parts: Sequence[str]) -> str:
    seen: set[str] = set()
    output: list[str] = []
    for raw in parts:
        text = _text(raw)
        if not text or text in seen:
            continue
        seen.add(text)
        output.append(text)
        if len(output) >= 12:
            break
    return "；".join(output)[:2400]


def _region(anchor_type: str, refs: Sequence[Mapping[str, Any]]) -> str:
    if anchor_type in _EXPERIENCE_TYPES:
        return "experience"
    if anchor_type in _JOB_TYPES:
        return "job"
    kinds = {
        _text(item.get("kind") or item.get("reference_kind")) for item in refs
    }
    if kinds & {"work_unit", "project", "indicator", "framework", "pao"}:
        return "experience"
    if kinds & {"job_unit", "job_capability", "skill_claim", "jd_unit"}:
        return "job"
    return "both"


def _text(value: Any) -> str:
    return str(value or "").strip()
