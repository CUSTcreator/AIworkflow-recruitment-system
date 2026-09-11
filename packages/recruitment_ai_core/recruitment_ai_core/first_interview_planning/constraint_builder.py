"""一面题单的确定性约束构建。

本模块不生成题干。它只把全部 Open InterviewTarget 转成可供 LLM 填写的题目槽位，保证每个
Target 至少被覆盖一次，额外问题也必须有明确、不同的核验角度。
"""
from __future__ import annotations

import hashlib
from typing import Any, Mapping

from recruitment_ai_core.screening_scoring.resume_experience.preset_models import get_preset_model

from .contracts import FirstInterviewPlanningInput
from .result_contracts import QuestionPlanningConstraintSet


_PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}


def build_question_planning_constraints(
    input_data: FirstInterviewPlanningInput,
) -> QuestionPlanningConstraintSet:
    """构建本次题单允许使用的 Target、证据、Rubric 与题目槽位。

    输入是冻结的 Open Target、岗位能力定义、候选人证据和题目数量上限；输出约束集不含题干，
    因而可重复执行并得到相同 Target 绑定和 slot_id。只有本函数允许决定“可让 LLM 生成哪些题”。
    """
    # 1. 仅接收当前仍为 open 的冻结目标；超过 V1 上限即失败，禁止静默丢弃。
    targets = [
        dict(item)
        for item in input_data.interview_targets
        if isinstance(item, Mapping) and str(item.get("status") or "open") == "open"
    ]
    if len(targets) > input_data.generation_config.max_open_interview_targets:
        raise ValueError("open_interview_target_limit_exceeded")

    # 2. 每个 Target 先物化一份快照，再保证至少一个主问题槽位，确保题目覆盖不依赖 LLM 自觉。
    target_snapshots = [
        _target_snapshot(target, index, input_data)
        for index, target in enumerate(targets, start=1)
    ]
    primary_slots = [_slot(snapshot, "primary") for snapshot in target_snapshots]
    remaining = max(0, input_data.generation_config.max_question_suggestions - len(primary_slots))
    # 3. 额外题只能来自同一 Target 的不同核验角度，绝不使用无目标归属的 filler 题。
    extra_slots = [
        slot
        for snapshot in target_snapshots
        for slot in _extra_slots(snapshot, input_data.generation_config.max_questions_per_target)
    ]
    extra_slots.sort(
        key=lambda item: (
            _PRIORITY_RANK.get(str(item.get("priority")), 9),
            int(item.get("target_order") or 0),
            str(item.get("probe_angle") or ""),
        )
    )
    slots = [*primary_slots, *extra_slots[:remaining]]
    warnings: list[str] = []
    if not target_snapshots:
        warnings.append("first_interview_no_open_target")
    if len(slots) < input_data.generation_config.max_question_suggestions and target_snapshots:
        warnings.append("first_interview_no_valid_extra_slot")
    return QuestionPlanningConstraintSet(
        target_snapshots=target_snapshots,
        question_slots=slots,
        generation_warnings=warnings,
    )


def _target_snapshot(
    target: dict[str, Any], order: int, input_data: FirstInterviewPlanningInput
) -> dict[str, Any]:
    """把一个持久化 InterviewTarget 投影为可审计的运行时快照。

    返回字段包括目标身份、核验目的、能力叶子定义、来源结果/证据、优先级和基础 Rubric；
    原始 Target 不在本函数修改。
    """
    target_type = str(target.get("target_type") or "job_capability")
    target_id = str(target.get("target_id") or "")
    definition = _leaf_definition(target_type, target_id, input_data.job_profile)
    purpose = str(target.get("purpose") or "verify_experience")
    trigger_code = str(target.get("trigger_code") or "")
    priority = _priority(target, purpose, trigger_code)
    evidence_ids = _strings(target.get("evidence_ids") or target.get("source_evidence_ids"))
    return {
        "interview_target_id": str(target.get("interview_target_id") or ""),
        "target_order": order,
        "purpose": purpose,
        "target_type": target_type,
        "target_id": target_id,
        "title": str(target.get("title") or definition.get("name") or target.get("verification_goal") or "待核验能力"),
        "verification_goal": str(target.get("verification_goal") or ""),
        "trigger_code": trigger_code,
        "priority": priority,
        "source_result_ids": _strings(target.get("source_result_ids")),
        "source_evidence_ids": evidence_ids,
        "source_evidence": _source_evidence(evidence_ids, input_data.evidence_records),
        "capability_definition": definition,
        "base_rubrics": _base_rubrics(target_type, target_id, definition, input_data),
    }


def _slot(snapshot: dict[str, Any], probe_angle: str) -> dict[str, Any]:
    """从一个目标快照生成一个不可变 QuestionSlot。

    direct_demonstration 对应现场表现题和 Rubric；其他目标默认是经历事实题。返回的 Target/证据/
    能力叶子关联后续不得由 LLM 改写。
    """
    purpose = str(snapshot["purpose"])
    direct = purpose == "direct_demonstration"
    if probe_angle == "primary":
        probe_angle = "direct_task" if direct else "fact_reconstruction"
    if probe_angle == "direct_task":
        question_type, expected_result_type, requires_rubric = (
            "direct_task", "interview_performance", True
        )
    elif probe_angle == "capability_probe":
        question_type, expected_result_type, requires_rubric = (
            "capability_probe", "interview_performance", True
        )
    else:
        question_type, expected_result_type, requires_rubric = (
            "experience_probe", "experience_fact", False
        )
    target_type = str(snapshot["target_type"])
    target_id = str(snapshot["target_id"])
    return {
        "slot_id": _stable_id("QS", str(snapshot["interview_target_id"]), probe_angle),
        "interview_target_ids": [str(snapshot["interview_target_id"])],
        "target_order": int(snapshot["target_order"]),
        "probe_angle": probe_angle,
        "question_type": question_type,
        "purpose": str(snapshot["purpose"]),
        "expected_result_type": expected_result_type,
        "target_job_capability_ids": [target_id] if target_type == "job_capability" and target_id else [],
        "target_preset_indicator_ids": [target_id] if target_type == "preset_indicator" and target_id else [],
        "source_evidence_ids": list(snapshot["source_evidence_ids"]),
        "base_rubrics": list(snapshot["base_rubrics"]) if requires_rubric else [],
        "requires_rubric": requires_rubric,
        "priority": str(snapshot["priority"]),
    }


def _extra_slots(snapshot: dict[str, Any], max_questions_per_target: int) -> list[dict[str, Any]]:
    """按目标目的和触发原因选择补充核验角度，而不是为凑数量随意新增问题。"""
    if max_questions_per_target <= 1:
        return []
    purpose = str(snapshot["purpose"])
    trigger = str(snapshot["trigger_code"])
    angles: list[str] = []
    if purpose == "direct_demonstration":
        angles.append("capability_probe")
    elif trigger in {"missing_core_evidence", "weak_core_result"}:
        angles.append("contribution_boundary")
    elif trigger == "high_result_narrow_proof":
        angles.append("evidence_crosscheck")
    return [_slot(snapshot, angle) for angle in angles[: max_questions_per_target - 1]]


def _leaf_definition(target_type: str, target_id: str, job_profile: Mapping[str, Any]) -> dict[str, Any]:
    if target_type == "job_capability":
        for item in job_profile.get("job_capabilities") or []:
            if isinstance(item, Mapping) and str(item.get("job_capability_id") or "") == target_id:
                return {
                    "name": str(item.get("capability_name") or item.get("capability_definition") or target_id),
                    "definition": str(item.get("capability_definition") or item.get("capability_name") or ""),
                    "assessment_mode": str(item.get("assessment_mode") or "experience"),
                    "quality_focus_ids": _strings(item.get("quality_focus_ids")),
                }
    return {"name": target_id, "definition": target_id, "assessment_mode": "experience"}


def _base_rubrics(
    target_type: str,
    target_id: str,
    definition: Mapping[str, Any],
    input_data: FirstInterviewPlanningInput,
) -> list[dict[str, Any]]:
    if target_type == "preset_indicator":
        try:
            preset = get_preset_model(
                input_data.job_profile.get("preset_model_id"),
                input_data.job_profile.get("preset_model_version"),
            )
            indicator = next(
                (item for item in preset.get("indicators", []) if item.get("indicator_id") == target_id),
                None,
            )
            if isinstance(indicator, Mapping):
                anchors = [
                    {"level": index, "description": str(description)}
                    for index, description in enumerate(indicator.get("interview_rubric", [])[:4], start=1)
                ]
                if anchors:
                    return [{"target_type": target_type, "target_id": target_id, "max_level": len(anchors), "level_anchors": anchors}]
        except Exception:
            pass
    name = str(definition.get("name") or target_id or "该能力")
    return [{
        "target_type": target_type,
        "target_id": target_id,
        "max_level": 4,
        "level_anchors": [
            {"level": 1, "description": f"只能概括说明{name}，缺少具体事实。"},
            {"level": 2, "description": f"能说明{name}的局部动作或基本原理。"},
            {"level": 3, "description": f"能说明{name}的核心动作、主要取舍和结果。"},
            {"level": 4, "description": f"能说明{name}的边界、失败处理和可验证结果。"},
        ],
    }]


def _source_evidence(ids: list[str], records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {str(item.get("evidence_id") or ""): item for item in records if isinstance(item, Mapping)}
    return [
        {
            "evidence_id": evidence_id,
            "evidence_type": str(by_id.get(evidence_id, {}).get("evidence_type") or "source"),
            "raw_text": str(by_id.get(evidence_id, {}).get("raw_text") or "")[:800],
        }
        for evidence_id in ids
    ]


def _priority(target: Mapping[str, Any], purpose: str, trigger: str) -> str:
    explicit = str(target.get("priority") or "")
    if explicit in _PRIORITY_RANK:
        return explicit
    if purpose == "direct_demonstration" or trigger in {"missing_core_evidence", "weak_core_result"}:
        return "high"
    return "medium"


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(item) for item in value if item))


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"