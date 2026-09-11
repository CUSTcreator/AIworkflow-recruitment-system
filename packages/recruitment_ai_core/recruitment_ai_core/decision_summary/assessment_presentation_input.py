"""把正式 AAV 的规则事实转换为 LLM 展示输入。

该输入只在运行时存在，不对应数据库表。它解决了 Signal 仅保存目标 ID、
来源结果 ID 的正常领域设计与页面必须展示中文名称、事实理由之间的边界问题。
"""
from __future__ import annotations

from typing import Any

from recruitment_ai_core.screening_scoring.resume_experience.preset_models import (
    get_preset_model,
)
from .recommendation_policy import (
    POLICY_VERSION,
    compute_recommendation_level,
    recommendation_display_text,
)


def build_assessment_presentation_inputs(
    *, stage: str, core_result: dict[str, Any], rule_result: dict[str, Any],
    job_profile: dict[str, Any], interview_targets: list[dict[str, Any]],
) -> dict[str, Any]:
    """为 V1/V2/V3 生成同形、可校验的摘要与核验重点输入。"""
    core, rule = dict(core_result or {}), dict(rule_result or {})
    target_index = _target_index(core, job_profile)
    result_index = _result_index(core)
    strengths, strength_context = _signals(
        rule.get("strength_signals") or rule.get("strengths") or [], target_index, result_index, "strength"
    )
    weaknesses, weakness_context = _signals(
        rule.get("weakness_signals") or rule.get("weaknesses") or [], target_index, result_index, "weakness"
    )
    targets, target_context = _targets(interview_targets, target_index, result_index)
    score_summary = _score_summary(core.get("score_result"))
    hard_result = rule.get("hard_screening_result") or {}
    level = compute_recommendation_level(
        score_summary,
        hard_screening_status=str(hard_result.get("status") or ""),
        has_critical_gap=_has_critical_gap(weaknesses, target_index),
    )
    return {
        "summary_input": {
            "stage": stage,
            "scores": dict(core.get("score_result") or {}),
            "strength_signals": strengths,
            "weakness_signals": weaknesses,
        },
        "focus_input": {"stage": stage, "interview_targets": targets},
        # 四项已冻结评分决定推荐程度；其余材料只用于解释结论。内部 ID 与原始
        # 证据关联仍留在后端，模型不参与任何业务对象绑定。
        "bundle_input": {
            # 推荐等级由后端策略冻结；LLM 只能解释该结论，不能重新判断通过与否。
            "recommendation": {
                "level": level,
                "display_text": recommendation_display_text(level, stage),
                "policy_version": POLICY_VERSION,
            },
            "score_summary": score_summary,
            "strengths": [_llm_signal(item, index) for index, item in enumerate(strengths, start=1)],
            "weaknesses": [_llm_signal(item, index) for index, item in enumerate(weaknesses, start=1)],
            "verification_focus": [_llm_target(item, index) for index, item in enumerate(targets, start=1)],
        },
        # 下列索引只给后端在 LLM 返回后回填证据；LLM 必须原样返回 input_index。
        "signal_context": {**strength_context, **weakness_context},
        "target_context": target_context,
    }


def _score_summary(value: Any) -> dict[str, float | None]:
    """Expose only the four page-facing V1/V2/V3 scores to the presentation LLM."""
    source = dict(value) if isinstance(value, dict) else {}

    def number(name: str) -> float | None:
        item = source.get(name)
        return float(item) if isinstance(item, (int, float)) and not isinstance(item, bool) else None

    return {
        "total": number("total"),
        "job_fit": number("job_fit"),
        "experience": number("experience"),
        "education": number("education"),
    }


def _target_index(core: dict[str, Any], job_profile: dict[str, Any]) -> dict[tuple[str, str], dict[str, str]]:
    output: dict[tuple[str, str], dict[str, str]] = {}
    for item in job_profile.get("job_capabilities") or []:
        if not isinstance(item, dict):
            continue
        target_id = str(item.get("job_capability_id") or "")
        if target_id:
            output[("job_capability", target_id)] = {
                "name": str(item.get("capability_name") or item.get("capability_definition") or "岗位能力要求"),
                "definition": str(item.get("capability_definition") or ""),
                "role": str(item.get("role") or "supporting").lower(),
            }
    model_id = str(job_profile.get("preset_model_id") or "")
    model_version = str(job_profile.get("preset_model_version") or "")
    try:
        preset = get_preset_model(model_id, model_version)
    except ValueError:
        preset = get_preset_model()
    for item in preset.get("indicators") or []:
        target_id = str(item.get("indicator_id") or "")
        if target_id:
            output[("preset_indicator", target_id)] = {
                "name": str(item.get("name") or "经历能力表现"),
                "definition": str(item.get("ideal_definition") or item.get("definition") or ""),
                "role": "preset_indicator",
            }
    return output


def _result_index(core: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """按来源结果 ID 提取确定性理由和证据，不计算或改写任何分数。"""
    output: dict[str, dict[str, Any]] = {}
    experience = dict(core.get("experience_result") or core.get("preset_experience_result") or {})
    for item in experience.get("work_unit_indicator_results") or []:
        if isinstance(item, dict) and item.get("work_unit_indicator_result_id"):
            output[str(item["work_unit_indicator_result_id"])] = {
                "summary": _first_text(item.get("reason")),
                "evidence_ids": _strings([item.get("work_unit_id")]),
            }
    for item in experience.get("project_indicator_results") or []:
        if not isinstance(item, dict) or not item.get("project_indicator_result_id"):
            continue
        evaluation = item.get("project_evaluation") if isinstance(item.get("project_evaluation"), dict) else {}
        output[str(item["project_indicator_result_id"])] = {
            "summary": _first_text(item.get("reason"), evaluation.get("reason")),
            "evidence_ids": _strings(evaluation.get("work_unit_ids") or []),
        }
    job = dict(core.get("job_result") or {})
    pairs = {
        str(item.get("pair_id") or ""): item
        for item in job.get("pair_assessments") or []
        if isinstance(item, dict) and item.get("pair_id")
    }
    for item in job.get("capability_results") or []:
        if not isinstance(item, dict) or not item.get("job_capability_result_id"):
            continue
        pair_ids = _strings([item.get("primary_pair_id"), *(item.get("supplemental_pair_ids") or [])])
        reasons = [str(pairs[value].get("reason") or "") for value in pair_ids if value in pairs]
        evidence_ids: list[str] = []
        for value in pair_ids:
            pair = pairs.get(value) or {}
            for evidence_id in _strings([*(pair.get("proof_work_unit_ids") or []), pair.get("evidence_id")]):
                if evidence_id not in evidence_ids:
                    evidence_ids.append(evidence_id)
        output[str(item["job_capability_result_id"])] = {
            "summary": _first_text(*reasons), "evidence_ids": evidence_ids,
        }
    return output


def _signals(rows: list[Any], target_index: dict[tuple[str, str], dict[str, str]], result_index: dict[str, dict[str, Any]], kind: str) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    values: list[dict[str, Any]] = []
    contexts: dict[str, dict[str, Any]] = {}
    for item in rows:
        if not isinstance(item, dict):
            continue
        source_type = str(item.get("source_type") or "")
        target_id = str(item.get("target_id") or "")
        # ``signal_id`` 仅供历史 V1 适配；正式 V1/V2/V3 一律使用稳定的 signal_key。
        signal_id = str(item.get("signal_key") or item.get("signal_id") or f"{kind}:{source_type}:{target_id}")
        target = target_index.get((source_type, target_id), {})
        result_ids = _strings(item.get("source_result_ids") or [])
        summaries = [result_index[value]["summary"] for value in result_ids if result_index.get(value, {}).get("summary")]
        evidence_ids: list[str] = []
        for value in result_ids:
            for evidence_id in result_index.get(value, {}).get("evidence_ids", []):
                if evidence_id not in evidence_ids:
                    evidence_ids.append(evidence_id)
        context = {
            "signal_id": signal_id, "source_type": source_type, "target_id": target_id,
            "target_name": target.get("name") or ("岗位能力要求" if source_type == "job_capability" else "经历能力表现"),
            "target_definition": target.get("definition") or "",
            "target_role": target.get("role") or "supporting",
            "source_result_ids": result_ids,
            "result_summary": _first_text(*summaries) or "当前结果缺少可展示的具体证据说明。",
            "evidence_ids": evidence_ids or _strings(item.get("evidence_ids") or []),
        }
        values.append(context)
        contexts[signal_id] = context
    return values, contexts


def _targets(rows: list[dict[str, Any]], target_index: dict[tuple[str, str], dict[str, str]], result_index: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    values: list[dict[str, Any]] = []
    contexts: dict[str, dict[str, Any]] = {}
    for item in rows:
        if not isinstance(item, dict) or not item.get("interview_target_id"):
            continue
        target_type, target_id = str(item.get("target_type") or ""), str(item.get("target_id") or "")
        target_key = str(item["interview_target_id"])
        target = target_index.get((target_type, target_id), {})
        result_ids = _strings(item.get("source_result_ids") or [])
        summaries = [result_index[value]["summary"] for value in result_ids if result_index.get(value, {}).get("summary")]
        context = {
            "interview_target_id": target_key, "status": str(item.get("status") or "open"), "purpose": str(item.get("purpose") or ""),
            "target_name": target.get("name") or str(item.get("title") or "待核验能力"),
            "target_definition": target.get("definition") or "",
            "trigger_code": str(item.get("trigger_code") or ""),
            "verification_goal": str(item.get("verification_goal") or ""),
            "source_result_summary": _first_text(*summaries),
            "evidence_ids": _strings(item.get("evidence_ids") or []),
        }
        values.append(context)
        contexts[target_key] = context
    return values, contexts


def _llm_signal(item: dict[str, Any], input_index: int) -> dict[str, Any]:
    """移除内部 ID、推荐和分数，只保留事实及稳定的数组位置。"""
    return {
        "input_index": input_index,
        "target_name": str(item.get("target_name") or "能力表现"),
        "target_definition": str(item.get("target_definition") or ""),
        "result_summary": str(item.get("result_summary") or ""),
    }


def _llm_target(item: dict[str, Any], input_index: int) -> dict[str, Any]:
    return {
        "input_index": input_index,
        "target_name": str(item.get("target_name") or "待核验能力"),
        "target_definition": str(item.get("target_definition") or ""),
        "reason": str(item.get("source_result_summary") or ""),
        "verification_goal": str(item.get("verification_goal") or ""),
    }


def _has_critical_gap(
    weaknesses: list[dict[str, Any]],
    target_index: dict[tuple[str, str], dict[str, str]],
) -> bool:
    """核心岗位能力的明确薄弱项限制最高推荐档位，避免分数掩盖关键缺口。"""
    return any(
        str(item.get("source_type") or "") == "job_capability"
        and target_index.get(("job_capability", str(item.get("target_id") or "")), {}).get("role") == "core"
        for item in weaknesses
        if isinstance(item, dict)
    )

def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text[:300]
    return ""


def _strings(values: Any) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if value)) if isinstance(values, list) else []


