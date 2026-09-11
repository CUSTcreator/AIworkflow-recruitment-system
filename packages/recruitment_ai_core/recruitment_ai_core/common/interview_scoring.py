from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any

from recruitment_ai_core.llm import call_json_llm
from recruitment_ai_core.screening_scoring.resume_experience.preset_models import get_preset_model


LEVEL_SCORE = {0: 0.0, 1: 0.35, 2: 0.60, 3: 0.77, 4: 0.87, 5: 1.0}
POSITIVE_JUDGEMENTS = {"partial", "support", "verified"}
NEGATIVE_JUDGEMENTS = {
    "not_support",
    "weak_contradiction",
    "contradicted",
    "strong_contradiction",
}
POSITIVE_UPDATE = {"partial": 0.40, "support": 0.70, "verified": 0.80}
CONFIRMATION_BONUS = {"partial": 0.02, "support": 0.04, "verified": 0.05}
NEGATIVE_UPDATE = {
    # not_support 仍供已有显式观察分调用方使用；新版拓扑流程会在评分前过滤它。
    "not_support": 0.40,
    "weak_contradiction": 0.25,
    "contradicted": 0.40,
    "strong_contradiction": 0.40,
}
SUPPORT_WEIGHTS = (0.04, 0.02)

JOB_LEVEL_ANCHORS = {
    0: "与该岗位能力无关，未形成有效支持。",
    1: "只支持邻近背景、简单接触或技能声明。",
    2: "支持部分相关行为，尚未覆盖核心行为。",
    3: "支持该岗位能力的核心行为。",
    4: "较完整支持核心行为及重要职责。",
    5: "接近完整支持能力定义及关键边界。",
}

EVALUATION_SCHEMA = {
    "type": "object",
    "required": ["evaluations"],
    "additionalProperties": False,
    "properties": {
        "evaluations": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "observation_id", "rubric_id", "target_type", "target_id",
                    "level", "source_refs", "reason",
                ],
                "additionalProperties": False,
                "properties": {
                    "observation_id": {"type": "string"},
                    "rubric_id": {"type": "string"},
                    "target_type": {
                        "type": "string",
                        "enum": ["job_capability", "preset_indicator"],
                    },
                    "target_id": {"type": "string"},
                    "level": {"type": "integer", "minimum": 0, "maximum": 5},
                    "source_refs": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "required": ["segment_id", "quote"],
                            "additionalProperties": False,
                            "properties": {
                                "segment_id": {"type": "string"},
                                "quote": {"type": "string", "minLength": 1},
                            },
                        },
                    },
                    "reason": {"type": "string", "maxLength": 300},
                },
            },
        }
    },
}


def assess_observation_targets(
    observations: list[dict[str, Any]],
    job_profile: dict[str, Any],
    llm_config: dict[str, Any] | None,
    *,
    workflow_name: str,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, bool]:
    pairs = _target_pairs(observations, job_profile)
    if not pairs:
        return [], None, False
    messages = [
        {
            "role": "system",
            "content": (
                "只使用当前Observation和目标判级。逐题记录使用题目已固化的evaluation_rubric；"
                "自由记录对当前岗位预设经历指标使用interview_rubric，对JDCapability严格使用"
                "job_capability_rubric的内容支持度锚点，不得改用独立程度、技术深度或即插即用程度判级。"
                "面试官明确结论按高可信评价处理；目标或评价范围不清时不扩展到其他能力。"
                "Observation中的候选人自述仍需具体事实支持。knowledge只评价JobCapability；"
                "reasoning和scenario评价已绑定的JobCapability，以及实际考察并已绑定的当前岗位预设经历指标。"
                "不得因模式类型扩展目标。必须返回每一输入pair，"
                "level不得超过max_level，引用必须是Observation source_refs中的连续原文。"
            ),
        },
        {"role": "user", "content": json.dumps({"pairs": pairs}, ensure_ascii=False)},
    ]
    response, trace = call_json_llm(
        workflow_name=workflow_name,
        messages=messages,
        schema_name="interview_observation_target_result_v1_0",
        settings_overrides=_json_mode_config(llm_config, workflow_name),
        json_schema=EVALUATION_SCHEMA,
    )
    if response is None:
        return [], trace, True
    normalized = _normalize_evaluations(response, pairs)
    return normalized, trace, len(normalized) != len(pairs)


def aggregate_round_results(
    results: list[dict[str, Any]],
    *,
    interview_round_id: str,
    job_profile: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    job_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    indicator_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        target = result["target_id"]
        if result["target_type"] == "job_capability":
            job_unit_id = result.get("job_unit_id") or result.get("target_id")
            if job_unit_id:
                job_groups[str(job_unit_id)].append(result)
        else:
            indicator_groups[target].append(result)

    evidence: list[dict[str, Any]] = []
    risks: list[dict[str, Any]] = []
    for target_id, items in job_groups.items():
        row, risk = _aggregate_target(
            items, interview_round_id, "job_unit", target_id
        )
        row["tested_job_capability_ids"] = sorted({
            str(item["target_id"]) for item in items
        })
        evidence.append(row)
        if risk:
            risks.append(risk)

    indicator_evidence: dict[str, dict[str, Any]] = {}
    for indicator_id, items in indicator_groups.items():
        row, risk = _aggregate_target(
            items, interview_round_id, "preset_indicator", indicator_id
        )
        indicator_evidence[indicator_id] = row
        if risk:
            risks.append(risk)

    job_profile = job_profile or {}
    preset_model = get_preset_model(
        job_profile.get("preset_model_id"),
        job_profile.get("preset_model_version"),
    )
    specs = {item["indicator_id"]: item for item in preset_model["indicators"]}
    by_framework: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for indicator_id, row in indicator_evidence.items():
        spec = specs.get(indicator_id)
        if spec:
            by_framework[spec["framework_id"]].append(row)
    for framework_id, rows in by_framework.items():
        positives = sorted(
            [row for row in rows if row.get("positive_score") is not None],
            key=lambda row: row["positive_score"], reverse=True,
        )
        negatives = sorted(
            [row for row in rows if row.get("negative_score") is not None],
            key=lambda row: row["negative_score"],
        )
        conflicts = [
            row["target_id"] for row in rows if row.get("conflict_target_ids")
        ]
        evidence.append({
            "round_evidence_id": _id("IRE", interview_round_id, "preset_framework", framework_id),
            "interview_round_id": interview_round_id,
            "target_type": "preset_framework",
            "target_id": framework_id,
            "tested_indicator_ids": sorted(row["target_id"] for row in rows),
            "positive_score": _support_score([row["positive_score"] for row in positives]),
            "negative_score": negatives[0]["negative_score"] if negatives else None,
            "positive_judgement": positives[0].get("positive_judgement") if positives else None,
            "negative_judgement": negatives[0].get("negative_judgement") if negatives else None,
            "observation_result_ids": [
                result_id for row in rows for result_id in row["observation_result_ids"]
            ],
            "conflict_target_ids": conflicts,
        })
    return evidence, risks


def aggregate_target_round_results(
    results: list[dict[str, Any]],
    *,
    interview_round_id: str,
    target_type: str,
    target_id: str,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """按既定证明足迹规则汇总单个拓扑锚点的一轮面评。

    新拓扑流程与旧 Observation 流程共用本函数：同一证明足迹只保留更强结果，
    独立正向证据最多取三项，随后由 ``apply_round_update`` 应用既定公式。
    """
    return _aggregate_target(results, interview_round_id, target_type, target_id)

def apply_round_update(
    before: float,
    round_evidence: dict[str, Any],
    *,
    max_negative_delta: float | None = None,
) -> dict[str, float]:
    if max_negative_delta is not None and max_negative_delta < 0:
        raise ValueError("max_negative_delta_must_be_non_negative")
    if round_evidence.get("conflict_target_ids"):
        return {
            "before_score": round(before, 6),
            "positive_delta": 0.0,
            "negative_delta": 0.0,
            "net_delta": 0.0,
            "after_score": round(before, 6),
        }
    positive = round_evidence.get("positive_score")
    negative = round_evidence.get("negative_score")
    up = 0.0
    if positive is not None:
        judgement = round_evidence.get("positive_judgement") or "partial"
        u = POSITIVE_UPDATE.get(judgement, 0.0)
        c = CONFIRMATION_BONUS.get(judgement, 0.0)
        up = u * max(0.0, positive - before) + c * min(before, positive) * (1.0 - before)
    down = 0.0
    if negative is not None:
        judgement = round_evidence.get("negative_judgement") or "not_support"
        d = NEGATIVE_UPDATE.get(judgement, 0.0)
        down = d * max(0.0, before - negative)
        if max_negative_delta is not None:
            down = min(down, max_negative_delta)
    after = min(1.0, max(0.0, before + up - down))
    return {
        "before_score": round(before, 6),
        "positive_delta": round(up, 6),
        "negative_delta": round(down, 6),
        "net_delta": round(after - before, 6),
        "after_score": round(after, 6),
    }


def _target_pairs(
    observations: list[dict[str, Any]], job_profile: dict[str, Any]
) -> list[dict[str, Any]]:
    capabilities = {
        item["job_capability_id"]: item
        for item in job_profile.get("job_capabilities", [])
        if item.get("job_capability_id")
    }
    job_profile = job_profile or {}
    preset_model = get_preset_model(
        job_profile.get("preset_model_id"),
        job_profile.get("preset_model_version"),
    )
    indicators = {item["indicator_id"]: item for item in preset_model["indicators"]}
    pairs: list[dict[str, Any]] = []
    for observation in observations:
        for target_id in observation.get("target_job_capability_ids", []):
            capability = capabilities.get(target_id)
            if capability:
                pairs.append(_pair(observation, "job_capability", capability, None))
        if observation.get("observation_mode") == "knowledge":
            continue
        for target_id in observation.get("target_preset_indicator_ids", []):
            indicator = indicators.get(target_id)
            if indicator:
                pairs.append(_pair(observation, "preset_indicator", indicator, None))
    return pairs


def _pair(
    observation: dict[str, Any], target_type: str,
    target: dict[str, Any], _: None,
) -> dict[str, Any]:
    target_id = target.get("job_capability_id") or target["indicator_id"]
    frozen = next((
        rubric for rubric in observation.get("evaluation_rubrics", [])
        if rubric.get("target_type") == target_type and rubric.get("target_id") == target_id
    ), None)
    if frozen:
        rubric_id = frozen.get("rubric_id") or _id("RUBRIC", target_type, target_id)
        max_level = min(5, max(0, int(frozen.get("max_level", 5))))
        anchors = frozen.get("level_anchors", [])
    elif target_type == "preset_indicator":
        rubric_id = _id("RUBRIC", "preset_experience_interview", target_id)
        max_level = 5
        anchors = [
            {"level": index, "description": description}
            for index, description in enumerate(target["interview_rubric"], start=1)
        ]
    else:
        rubric_id = _id("RUBRIC", "job_capability", target_id)
        max_level = 5
        anchors = [
            {"level": level, "description": description}
            for level, description in JOB_LEVEL_ANCHORS.items() if level > 0
        ]
    return {
        "observation_id": observation["observation_id"],
        "observation_mode": observation["observation_mode"],
        "interviewer_judgement": observation["interviewer_judgement"],
        "interviewer_note": observation.get("interviewer_note", ""),
        "scenario_id": observation.get("scenario_id"),
        "source_refs": observation.get("source_refs", []),
        "rubric_id": rubric_id,
        "target_type": target_type,
        "target_id": target_id,
        "job_unit_id": target.get("job_unit_id") if target_type == "job_capability" else None,
        "target_definition": target.get("capability_definition") or target.get("ideal_definition"),
        "max_level": max_level,
        "level_anchors": anchors,
    }


def _normalize_evaluations(
    payload: dict[str, Any], pairs: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    allowed = {
        (pair["observation_id"], pair["target_type"], pair["target_id"]): pair
        for pair in pairs
    }
    output, seen = [], set()
    for row in payload.get("evaluations", []):
        key = (row.get("observation_id"), row.get("target_type"), row.get("target_id"))
        pair = allowed.get(key)
        if not pair or key in seen or row.get("rubric_id") != pair["rubric_id"]:
            continue
        level = row.get("level")
        refs = row.get("source_refs")
        if not isinstance(level, int) or not 0 <= level <= pair["max_level"]:
            continue
        if not _valid_refs(refs, pair.get("source_refs", [])):
            continue
        seen.add(key)
        output.append({
            "result_id": _id("OTR", *[str(value) for value in key]),
            "observation_id": row["observation_id"],
            "rubric_id": row["rubric_id"],
            "target_type": row["target_type"],
            "target_id": row["target_id"],
            "job_unit_id": pair.get("job_unit_id"),
            "level": level,
            "score": LEVEL_SCORE[level],
            "interviewer_judgement": pair["interviewer_judgement"],
            "scenario_id": pair.get("scenario_id"),
            "source_refs": refs,
            "reason": str(row.get("reason") or "")[:300],
        })
    return output


def _aggregate_target(
    items: list[dict[str, Any]], interview_round_id: str,
    target_type: str, target_id: str,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    unique: dict[str, dict[str, Any]] = {}
    for item in items:
        footprint = str(item.get("scenario_id") or item["observation_id"])
        previous = unique.get(footprint)
        if previous is None or _stronger(item, previous):
            unique[footprint] = item
    positives = sorted(
        [item for item in unique.values() if item["interviewer_judgement"] in POSITIVE_JUDGEMENTS],
        key=lambda item: item["score"], reverse=True,
    )[:3]
    negatives = sorted(
        [item for item in unique.values() if item["interviewer_judgement"] in NEGATIVE_JUDGEMENTS],
        key=lambda item: item["score"],
    )
    # 不同面评断言对同一宽泛能力给出优点和缺点是正常情况，应由既有
    # up/down 公式计算净变化。judgement=contradicted 表示对能力的负向证据，
    # 并不表示两条来源数据互相矛盾，不能据此制造数据冲突并冻结本轮更新。
    row = {
        "round_evidence_id": _id("IRE", interview_round_id, target_type, target_id),
        "interview_round_id": interview_round_id,
        "target_type": target_type,
        "target_id": target_id,
        "positive_score": _support_score([item["score"] for item in positives]),
        "negative_score": negatives[0]["score"] if negatives else None,
        "positive_judgement": positives[0]["interviewer_judgement"] if positives else None,
        "negative_judgement": negatives[0]["interviewer_judgement"] if negatives else None,
        "observation_result_ids": [item["result_id"] for item in unique.values()],
        "conflict_target_ids": [],
    }
    return row, None


def _support_score(scores: list[float]) -> float | None:
    if not scores:
        return None
    values = [*scores[:3], 0.0, 0.0]
    return round(min(1.0, values[0] + SUPPORT_WEIGHTS[0] * values[1] + SUPPORT_WEIGHTS[1] * values[2]), 6)


def _stronger(current: dict[str, Any], previous: dict[str, Any]) -> bool:
    current_negative = current["interviewer_judgement"] in NEGATIVE_JUDGEMENTS
    previous_negative = previous["interviewer_judgement"] in NEGATIVE_JUDGEMENTS
    if current_negative != previous_negative:
        return current_negative
    return current["score"] < previous["score"] if current_negative else current["score"] > previous["score"]


def _valid_refs(value: Any, source_refs: list[dict[str, Any]]) -> bool:
    if not isinstance(value, list) or not value:
        return False
    source = {
        (str(item.get("segment_id") or ""), str(item.get("quote") or ""))
        for item in source_refs
    }
    return all(
        (str(item.get("segment_id") or ""), str(item.get("quote") or "")) in source
        for item in value if isinstance(item, dict)
    ) and all(isinstance(item, dict) for item in value)


def _json_mode_config(config: dict[str, Any] | None, workflow: str) -> dict[str, Any]:
    merged = dict(config or {})
    workflows = {
        key: dict(value) for key, value in (merged.get("workflows") or {}).items()
        if isinstance(value, dict)
    }
    current = dict(workflows.get(workflow, {}))
    current.update({"json_mode": True, "strict_json_schema": False})
    workflows[workflow] = current
    merged["workflows"] = workflows
    return merged


def _id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"
