"""已发布评估版本的变化读模型。

仅把 AAV 的 core / rule / presentation 结果投影给正式页面；不计算分数、
不调用模型、也不保存页面专用对象。
"""
from __future__ import annotations

from typing import Any, Mapping

from backend.app.models.entities import ApplicationAssessmentVersion


def assessment_change_view(row: ApplicationAssessmentVersion | None) -> dict[str, Any] | None:
    """返回 V2/V3 相对前一已发布版本的展示变化；空版本返回 ``None``。"""
    if row is None:
        return None
    core = _mapping(row.core_result_json)
    rule = _mapping(row.rule_result_json)
    presentation = _mapping(row.presentation_json)
    round_change = _round_change(presentation)
    return {
        "assessmentVersionId": row.assessment_version_id,
        "stage": row.stage,
        "previousAssessmentVersionId": row.previous_assessment_version_id,
        "scoreChanges": [_score_change(item) for item in _items(core.get("score_changes"))],
        "capabilityChanges": (round_change or {}).get("capabilityChanges", []),
        "roundChangeSummary": round_change,
        "strengths": _signals(rule, presentation, "strengths"),
        "weaknesses": _signals(rule, presentation, "weaknesses"),
        "verificationFocus": _targets(rule, presentation),
    }


def _score_change(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "metric": value.get("metric"),
        "previousValue": _number(value.get("previous_value")),
        "currentValue": _number(value.get("current_value")),
        "delta": _number(value.get("delta")),
    }


def _signals(rule: Mapping[str, Any], presentation: Mapping[str, Any], kind: str) -> list[dict[str, Any]]:
    signal_field = "strength_signals" if kind == "strengths" else "weakness_signals"
    current = _items(rule.get(signal_field))
    comparison = _mapping(rule.get("signal_comparison"))
    raw = _items(comparison.get(kind)) or current
    text_by_key = {str(item.get("signal_key") or ""): item for item in _items(presentation.get(kind))}
    result: list[dict[str, Any]] = []
    for item in raw:
        key = str(item.get("signal_key") or item.get("signal_id") or "")
        text = text_by_key.get(key, {})
        result.append({
            "signalKey": key,
            "changeStatus": item.get("change_status"),
            "title": str(text.get("title") or item.get("title") or item.get("target_id") or "能力表现"),
            "summary": str(text.get("summary") or item.get("reason") or ""),
            "currentRank": item.get("current_rank"),
            "previousRank": item.get("previous_rank"),
            "evidenceIds": _strings(text.get("evidence_ids") or item.get("evidence_ids")),
        })
    return result


def _targets(rule: Mapping[str, Any], presentation: Mapping[str, Any]) -> list[dict[str, Any]]:
    updates = _items(rule.get("target_updates")) or _items(rule.get("interview_target_drafts"))
    text_by_id = {str(item.get("target_id") or item.get("interview_target_id") or ""): item for item in _items(presentation.get("verification_focus"))}
    result: list[dict[str, Any]] = []
    for item in updates:
        target_id = str(item.get("interview_target_id") or item.get("target_id") or "")
        text = text_by_id.get(target_id, {})
        result.append({
            "targetId": target_id,
            "status": str(text.get("status") or item.get("status") or "open"),
            "title": str(text.get("title") or item.get("title") or item.get("target_id") or "待核验事项"),
            "reason": str(text.get("reason") or item.get("resolution_note") or ""),
            "goal": str(text.get("goal") or item.get("verification_goal") or ""),
            "evidenceIds": _strings(text.get("evidence_ids") or item.get("evidence_ids")),
        })
    return result


def _round_change(presentation: Mapping[str, Any]) -> dict[str, Any] | None:
    value = _mapping(presentation.get("round_change_summary"))
    if not value:
        return None
    return {
        "title": str(value.get("title") or "本轮变化"),
        "summary": str(value.get("summary") or ""),
        "scoreChanges": [_score_change(item) for item in _items(value.get("score_changes"))],
        "capabilityChanges": [{
            "resultRef": str(item.get("result_ref") or ""),
            "capabilityId": str(item.get("capability_id") or ""),
            "previousScore": _capability_points(item.get("previous_score")),
            "currentScore": _capability_points(item.get("current_score")),
            "delta": _capability_points(item.get("delta")),
            "reasonCode": str(item.get("reason_code") or ""),
            "evidenceIds": _strings(item.get("evidence_ids")),
        } for item in _items(value.get("capability_changes"))],
    }


def _capability_points(value: Any) -> float | None:
    raw = _number(value)
    return raw * 100 if raw is not None and abs(raw) <= 1 else raw


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _items(value: Any) -> list[dict[str, Any]]:
    return [dict(item) for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []


def _strings(value: Any) -> list[str]:
    return list(dict.fromkeys(str(item) for item in value if item)) if isinstance(value, list) else []


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None