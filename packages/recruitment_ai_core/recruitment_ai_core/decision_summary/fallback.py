from __future__ import annotations

from typing import Any

from .presentation_safety import safe_display_text


def build_fallback_presentation_bundle(bundle_input: dict[str, Any]) -> dict[str, Any]:
    """LLM 不可用时按后端等级和冻结事实生成同形摘要，不改变评分。"""
    return {
        "summary": {
            "strength_sentence": "",
            "risk_sentence": "",
            "action_sentence": "",
        },
        "strengths": [
            {"input_index": index,
             "title": safe_display_text(item.get("target_name"), "优势表现", 40),
             "summary": safe_display_text(item.get("result_summary") or item.get("target_definition"), "已有材料支持该项判断。", 120)}
            for index, item in enumerate(bundle_input.get("strengths", []), start=1)
        ],
        "weaknesses": [
            {"input_index": index,
             "title": safe_display_text(item.get("target_name"), "薄弱项", 40),
             "summary": safe_display_text(item.get("result_summary") or item.get("target_definition"), "当前材料显示该项仍需关注。", 120)}
            for index, item in enumerate(bundle_input.get("weaknesses", []), start=1)
        ],
        "verification_focus": [
            {"input_index": index,
             "title": safe_display_text(item.get("target_name"), "核验能力事实", 50),
             "reason": safe_display_text(item.get("reason"), "该项需要在本轮进一步确认。", 120),
             "verification_goal": safe_display_text(item.get("verification_goal"), "确认相关事实和候选人的实际贡献。", 160)}
            for index, item in enumerate(bundle_input.get("verification_focus", []), start=1)
        ],
    }


def build_summary_text(
    bundle_input: dict[str, Any],
    summary: dict[str, Any] | None = None,
) -> str:
    """组合不超过 100 字的具体摘要正文；推荐标签由页面单独展示。"""
    supplied = dict(summary or {})
    strengths = list(bundle_input.get("strengths") or [])
    weaknesses = list(bundle_input.get("weaknesses") or [])
    focuses = list(bundle_input.get("verification_focus") or [])
    strength = _usable_sentence(supplied.get("strength_sentence")) or _fact(
        strengths, "result_summary", "当前材料未形成明确的优势证据。"
    )
    risk = _usable_sentence(supplied.get("risk_sentence")) or _fact(
        weaknesses, "result_summary", "当前材料未发现明确的直接风险。"
    )
    action = _usable_sentence(supplied.get("action_sentence")) or _fact(
        focuses, "verification_goal", "建议结合岗位要求核实关键事实。"
    )
    # 先保留优势和风险；只有剩余字数足够时才加入第三句行动建议。
    first = _short_sentence(strength, 42)
    second = _short_sentence(risk, 42)
    result = _join_sentences([first, second])
    third = _short_sentence(action, 24)
    if third and len(_join_sentences([result, third])) <= 100:
        result = _join_sentences([result, third])
    result = result[:99].rstrip("，。；、 ")
    return result + "。"


def _fact(items: list[Any], field: str, fallback: str) -> str:
    for item in items:
        if isinstance(item, dict):
            value = str(item.get(field) or item.get("target_definition") or "").strip()
            if value:
                return safe_display_text(value, fallback, 70)
    return fallback


def _usable_sentence(value: Any) -> str:
    text = str(value or "").strip()
    if not text or text in {"综合表现良好。", "优缺点并存。", "建议后续进一步了解。"}:
        return ""
    return text


def _short_sentence(value: str, limit: int) -> str:
    text = "".join(value.split())
    if not text:
        return ""
    text = text.rstrip("，。；、 ")
    if len(text) <= limit:
        return text + "。"
    # 优先在标点处收束，避免把摘要裁成半个词或半句话。
    for mark in ("。", "；", "，", "、"):
        position = text.rfind(mark, 0, limit)
        if position >= max(8, limit // 2):
            return text[:position] + "。"
    return text[: max(1, limit - 1)] + "…"


def _join_sentences(values: list[str]) -> str:
    return "".join(value for value in values if value)



