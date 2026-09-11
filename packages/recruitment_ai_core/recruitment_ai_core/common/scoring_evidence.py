from __future__ import annotations

"""评分运行时证据。

本模块只在一次算法调用期间组装简历事实、经历评估结果与面试解析更新；
它不是数据库实体，也不会生成独立版本或持久化 ID。
"""

from copy import deepcopy
from typing import Any


def build_scoring_evidence(
    *,
    resume_profile: dict[str, Any],
    resume_experience_result: dict[str, Any] | None = None,
    interview_parse_results: list[dict[str, Any]] | None = None,
    preset_model_id: str | None = None,
    preset_model_version: str | None = None,
) -> dict[str, Any]:
    """由明确的持久化来源组装本次评分所需的临时证据。"""
    experience_result = resume_experience_result or {}
    projects = deepcopy(
        experience_result.get("project_experience_assessments") or []
    )
    evidence = {
        "resume_profile_id": resume_profile.get("resume_profile_version_id"),
        "preset_model_id": preset_model_id,
        "preset_model_version": preset_model_version,
        "project_assessments": projects,
        "projects": _project_index(resume_profile, projects),
        "work_units": _work_units(projects),
        "skill_claims": _skill_claims(resume_profile),
        "project_evidence": _project_evidence(projects),
        "assertions": [],
        "source_interview_record_ids": [],
        "affected_project_ids": [],
        "corrected_skill_claim_ids": [],
        "excluded_skill_claim_ids": [],
    }
    for parse_result in interview_parse_results or []:
        apply_interview_parse_result(evidence, parse_result)
    return evidence


def apply_interview_parse_result(
    scoring_evidence: dict[str, Any],
    parse_result: dict[str, Any],
) -> dict[str, Any]:
    """将一份已持久化的面评解析结果应用到运行时证据，不创建快照对象。"""
    assertions = {
        str(item.get("assertionId") or item.get("assertion_id")): dict(item)
        for item in scoring_evidence.get("assertions", [])
        if isinstance(item, dict) and (item.get("assertionId") or item.get("assertion_id"))
    }
    for item in parse_result.get("assertions", []) or []:
        if isinstance(item, dict) and (item.get("assertionId") or item.get("assertion_id")):
            assertions[str(item.get("assertionId") or item.get("assertion_id"))] = dict(item)
    scoring_evidence["assertions"] = list(assertions.values())
    record_ids = list(scoring_evidence.get("source_interview_record_ids", []))
    for record_id in parse_result.get("source_record_ids") or []:
        if record_id and record_id not in record_ids:
            record_ids.append(record_id)
    scoring_evidence["source_interview_record_ids"] = record_ids
    return scoring_evidence


def _project_index(
    resume_profile: dict[str, Any],
    projects: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    contexts = {
        str(item.get("experience_unit_id") or item.get("project_id")): list(
            item.get("context_items") or []
        )
        for item in resume_profile.get("experience_units", [])
    }
    return [
        {
            "project_id": item["project_id"],
            "name": item.get("project_name") or item["project_id"],
            "project_context": contexts.get(item["project_id"], []),
        }
        for item in projects
        if item.get("project_id")
    ]


def _work_units(projects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    indicator_results: dict[str, list[dict[str, Any]]] = {}
    for project in projects:
        for result in project.get("work_unit_indicator_results", []):
            if result.get("work_unit_id"):
                indicator_results.setdefault(result["work_unit_id"], []).append(result)
    return [
        {
            **unit,
            "is_current": True,
            "work_unit_indicator_results": indicator_results.get(
                unit.get("work_unit_id"), []
            ),
        }
        for project in projects
        for unit in project.get("work_units", [])
        if unit.get("work_unit_id")
    ]


def _project_evidence(projects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        item["project_evidence"]
        for item in projects
        if is_usable_project_evidence(item.get("project_evidence"))
    ]


def is_usable_project_evidence(value: Any) -> bool:
    """判断项目级证据是否含有至少一项可用于岗位能力配对的事实。

    项目经历活动在外部调用重试耗尽后，会保留一个 ``unassessed`` 的项目
    结果，以便经历能力链路能够继续完成。但该占位结果的 P-A-O、项目指标
    和 WorkUnit 都为空，不能作为岗位能力的候选证据；否则会产生没有事实
    基础的配对请求，并额外增加一次 LLM 失败机会。

    任一部分仍有有效内容时都必须保留：P-A-O 局部校验失败不应连带丢弃
    已通过校验的项目指标或 WorkUnit。
    """
    if not isinstance(value, dict):
        return False
    pao = value.get("project_pao_result")
    has_pao = isinstance(pao, dict) and any(
        isinstance(pao.get(part), dict)
        and bool(str(pao[part].get("summary") or "").strip())
        for part in ("problem", "approach", "outcome")
    )
    has_project_indicator = bool(value.get("project_indicator_results"))
    has_work_unit = bool(value.get("work_units") or value.get("work_unit_ids"))
    return has_pao or has_project_indicator or has_work_unit


def _skill_claims(resume_profile: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(item) for item in resume_profile.get("skill_claims", [])
        if isinstance(item, dict)
    ]
