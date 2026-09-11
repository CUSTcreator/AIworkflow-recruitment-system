"""已确认题单与面评解析之间的稳定 Target 绑定。"""
from __future__ import annotations

from typing import Any


def plan_bindings(plan: Any) -> dict[str, list[str]]:
    """读取面试官最终确认的逐题 Target 绑定。

    已确认题目优先于规划草稿，保证后续 V2/V3 解析使用实际执行的题单，
    而非 LLM 或题单规划阶段已经被人工修改过的旧建议。
    """
    if not isinstance(plan, dict):
        return {}
    candidates = [
        plan.get("confirmed_questions"),
        _mapping(plan.get("confirmed_guide")).get("questions"),
        _mapping(plan.get("rule_result")).get("validated_questions"),
        _mapping(plan.get("presentation")).get("question_suggestions"),
        _mapping(_mapping(plan.get("presentation")).get("draft_guide")).get("questions"),
        _mapping(plan.get("core_result")).get("questionDraftProposals"),
    ]
    rows: list[Any] = []
    for candidate in candidates:
        if isinstance(candidate, list):
            rows.extend(candidate)
    result: dict[str, list[str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        question_id = str(row.get("question_id") or row.get("questionId") or "")
        target_ids = (
            row.get("interview_target_ids")
            or row.get("interviewTargetIds")
            or row.get("verificationTargetIds")
            or []
        )
        if question_id and question_id not in result and isinstance(target_ids, list):
            result[question_id] = list(dict.fromkeys(str(item) for item in target_ids if item))
    return result


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}
