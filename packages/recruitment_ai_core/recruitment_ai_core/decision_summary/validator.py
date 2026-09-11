from __future__ import annotations

from typing import Any

from .presentation_safety import safe_display_text


def validate_presentation_bundle(payload: dict[str, Any], bundle_input: dict[str, Any]) -> dict[str, Any]:
    """校验摘要句和三块文案；推荐等级只来自后端策略输入。"""
    if not isinstance(payload, dict):
        payload = {}
    _validate_input_sequence(list(bundle_input.get("strengths") or []), "strength")
    _validate_input_sequence(list(bundle_input.get("weaknesses") or []), "weakness")
    _validate_input_sequence(list(bundle_input.get("verification_focus") or []), "verification_focus")
    summary = payload.get("summary")
    if not isinstance(summary, dict):
        summary = {}
    return {
        "summary": {
            "strength_sentence": _safe_sentence(summary.get("strength_sentence")),
            "risk_sentence": _safe_sentence(summary.get("risk_sentence")),
            "action_sentence": _safe_sentence(summary.get("action_sentence")),
        },
        "strengths": _validate_signal_groups(payload.get("strengths"), list(bundle_input.get("strengths") or []), "strength"),
        "weaknesses": _validate_signal_groups(payload.get("weaknesses"), list(bundle_input.get("weaknesses") or []), "weakness"),
        "verification_focus": _validate_focus_items(payload.get("verification_focus"), list(bundle_input.get("verification_focus") or [])),
    }


def _validate_focus_items(value: Any, inputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    output = []
    max_index = len(inputs)
    seen: set[int] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        position = item.get("input_index")
        if isinstance(position, bool) or not isinstance(position, int) or not 1 <= position <= max_index or position in seen:
            continue
        try:
            output.append({"input_index": position, "title": _text(item.get("title"), 50, "待核验能力事实"), "reason": _text(item.get("reason"), 120, "该项需要进一步确认。"), "verification_goal": _text(item.get("verification_goal"), 160, "确认相关事实和实际贡献。")})
        except ValueError:
            continue
        seen.add(position)
    return output


def _validate_signal_groups(value: Any, inputs: list[dict[str, Any]], prefix: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    output = []
    max_index = len(inputs)
    seen: set[int] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        position = item.get("input_index")
        if isinstance(position, bool) or not isinstance(position, int) or not 1 <= position <= max_index or position in seen:
            continue
        try:
            output.append({"input_index": position, "title": _text(item.get("title"), 40, "能力表现"), "summary": _text(item.get("summary"), 120, "已有材料支持该项判断。")})
        except ValueError:
            continue
        seen.add(position)
    return output


def _validate_input_index(value: Any, expected: int, prefix: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value != expected:
        raise ValueError(f"{prefix}_order_invalid")


def _validate_input_sequence(inputs: list[Any], prefix: str) -> None:
    for expected_index, item in enumerate(inputs, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"{prefix}_input_invalid")
        _validate_input_index(item.get("input_index"), expected_index, f"{prefix}_input")


def _safe_text(value: Any, limit: int, fallback: str) -> str:
    try:
        return _text(value, limit, fallback)
    except ValueError:
        return fallback


def _safe_sentence(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return _text(text, 70, "")
    except ValueError:
        return ""


def _text(value: Any, limit: int, fallback: str) -> str:
    """拒绝内部 ID、模型等级、变量名和裸评分，避免技术细节泄露到页面。"""
    text = str(value or "").strip()
    if not text or len(text) > limit:
        raise ValueError("display_text_invalid")
    # 只替换当前字段，不影响同一 Bundle 中其他已经合格的文案。
    return safe_display_text(text, fallback, limit)




