"""V1 评分拓扑的内存投影。

本模块从已完成的初筛核心结果中提取锚点和显式聚合关系，供发布层写入
``AssessmentTopologyDefinition``、``AssessmentTopologyNode`` 等规范化表。
它不是页面 DTO，也不是数据库中的正式 Snapshot：返回值中的 ``source`` 与
``topologyHash`` 仅用于本次发布投影和历史兼容，不能作为 V2/V3 的业务读取入口。

评分核心回答“分数是多少”；本投影回答“已激活哪些锚点、它们如何聚合”。实际的
每版分数状态由 ``AssessmentTopologyNodeState`` 单独保存。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping


TOPOLOGY_SCHEMA_VERSION = "assessment_topology_snapshot_v1"


def build_v1_topology_snapshot(core_result: Mapping[str, Any], *, resume_profile: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """从已归一化的 V1 核心结果构造一次性内存拓扑投影。

    本函数不重新评分、不补造缺失节点，也不会从简历或岗位画像重新发现证据。
    缺失的显式关联只会导致对应边缺席；后续 V2/V3 必须以发布层最终写入的规范化
    Definition 为准，不能为了“补全”而在运行时新增 Pair 或激活新的指标。
    """
    experience = _mapping(core_result.get("experience_result"))
    job = _mapping(core_result.get("job_result"))
    resume = _mapping(resume_profile)
    anchors: list[dict[str, Any]] = []
    relations: list[dict[str, str]] = []

    work_units = _anchors("work_unit_indicator", _items(experience.get("work_unit_indicator_results")), _work_unit_indicator_key, ("work_unit_id", "project_id", "indicator_id", "score", "level"))
    project_pao = _anchors("project_pao", _items(experience.get("project_pao_results")), _project_pao_key, ("project_id", "pao_id", "dimension", "score", "level"))
    project_indicators = _anchors("project_indicator", _items(experience.get("project_indicator_results")), _project_indicator_key, ("project_id", "indicator_id", "score", "level"))
    project_frameworks = _anchors("project_framework", _items(experience.get("project_framework_results")), _project_framework_key, ("project_id", "framework_id", "score", "level"))
    candidate_frameworks = _anchors("candidate_framework", _items(experience.get("candidate_framework_results")), lambda item: _text(item.get("framework_id")), ("framework_id", "score", "level"))
    skill_claims = _anchors("skill_claim", _items(resume.get("skill_claims")), lambda item: _text(item.get("skill_claim_id")), ("skill_claim_id", "skill_id", "claim_type", "effective", "level"))
    pairs = _anchors("job_capability_evidence_pair", _items(job.get("pair_assessments")), lambda item: _text(item.get("pair_id")), ("pair_id", "job_capability_id", "evidence_id", "evidence_type", "evidence_ref", "score", "level"))
    capabilities = _anchors("job_capability", _items(job.get("capability_results") or job.get("job_capability_results")), lambda item: _text(item.get("job_capability_id")), ("job_capability_id", "job_unit_id", "score", "level"))
    job_units = _anchors("job_unit", _items(job.get("job_unit_results")), lambda item: _text(item.get("job_unit_id")), ("job_unit_id", "score", "level"))
    anchors.extend(work_units + project_pao + project_indicators + project_frameworks + candidate_frameworks)
    anchors.extend(skill_claims + pairs + capabilities + job_units)

    _link_by_shared_fields(relations, work_units, project_indicators, "aggregates_to", "project_id", "indicator_id")
    _link_by_shared_fields(relations, project_pao, project_indicators, "aggregates_to", "project_id")
    _link_by_shared_fields(relations, project_indicators, project_frameworks, "aggregates_to", "project_id")
    _link_by_shared_fields(relations, project_frameworks, candidate_frameworks, "aggregates_to", "framework_id")
    _link_skill_claims_to_pairs(relations, skill_claims, pairs)
    _link_by_shared_fields(relations, pairs, capabilities, "aggregates_to", "job_capability_id")
    _link_by_shared_fields(relations, capabilities, job_units, "aggregates_to", "job_unit_id")

    payload: dict[str, Any] = {"schemaVersion": TOPOLOGY_SCHEMA_VERSION, "anchors": sorted(anchors, key=lambda item: item["anchorId"]), "relations": sorted(relations, key=lambda item: (item["childAnchorId"], item["parentAnchorId"])), "sourceCoreHash": _stable_hash(dict(core_result))}
    # 这是旧调用方所需的内存投影哈希，包含首轮状态；正式持久化改由
    # build_v1_persistence_plan 分别计算不含分数的 structure_hash 与 state_hash。
    payload["topologyHash"] = _stable_hash(payload)
    return payload


def _anchors(kind: str, items: Iterable[Mapping[str, Any]], key, fields: tuple[str, ...]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in items:
        identifier = key(item)
        if identifier:
            result.append({"anchorId": f"{kind}:{identifier}", "anchorType": kind, "source": {field: item[field] for field in fields if field in item and item[field] is not None}})
    return result


def _link_by_shared_fields(relations: list[dict[str, str]], children: Iterable[Mapping[str, Any]], parents: Iterable[Mapping[str, Any]], relation: str, *fields: str) -> None:
    parent_index = {tuple(_text(parent.get("source", {}).get(field)) for field in fields): parent["anchorId"] for parent in parents if all(_text(parent.get("source", {}).get(field)) for field in fields)}
    for child in children:
        source = _mapping(child.get("source"))
        parent_id = parent_index.get(tuple(_text(source.get(field)) for field in fields))
        if parent_id:
            relations.append({"childAnchorId": child["anchorId"], "parentAnchorId": parent_id, "relation": relation})


def _link_skill_claims_to_pairs(relations: list[dict[str, str]], claims: Iterable[Mapping[str, Any]], pairs: Iterable[Mapping[str, Any]]) -> None:
    """仅连接 V1 已被 Pair 使用的 SkillClaim；不能在面后阶段新增证据绑定。"""
    claims_by_id = {str(item.get("source", {}).get("skill_claim_id") or ""): item["anchorId"] for item in claims}
    for pair in pairs:
        source = _mapping(pair.get("source"))
        if _text(source.get("evidence_type")) != "skill_claim":
            continue
        claim_id = _text(source.get("evidence_id"))
        if claim_id in claims_by_id:
            relations.append({"childAnchorId": claims_by_id[claim_id], "parentAnchorId": pair["anchorId"], "relation": "evidence_for"})

def _work_unit_indicator_key(item: Mapping[str, Any]) -> str:
    return ":".join(part for part in (_text(item.get("work_unit_id")), _text(item.get("indicator_id"))) if part)


def _project_indicator_key(item: Mapping[str, Any]) -> str:
    return ":".join(part for part in (_text(item.get("project_id")), _text(item.get("indicator_id"))) if part)


def _project_pao_key(item: Mapping[str, Any]) -> str:
    return ":".join(part for part in (_text(item.get("project_id")), _text(item.get("pao_id")) or _text(item.get("dimension"))) if part)


def _project_framework_key(item: Mapping[str, Any]) -> str:
    return ":".join(part for part in (_text(item.get("project_id")), _text(item.get("framework_id"))) if part)


def _items(value: Any) -> list[Mapping[str, Any]]:
    return [item for item in (value or []) if isinstance(item, Mapping)]


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _stable_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()