from __future__ import annotations

"""硬性筛选计算入口。

优先使用可确定的简历事实和规则运算；只有自由文本语义条件才调用 LLM，并将无法
可靠判断的结果保留为人工复核，而不是武断拒绝。
"""

import json
import re
from typing import Any

from recruitment_ai_core.llm import call_json_llm, load_llm_settings
from .facts import build_hard_screening_facts


WORKFLOW_NAME = "hard_screening"
SUPPORTED_SCOPES = {
    "full_resume", "education", "skills", "work_experience",
    "project_experience", "certifications", "awards",
}
SUPPORTED_OPERATORS = {
    "exists", "contains_any", "contains_all", "not_contains_any",
    "degree_at_least", "education_status_is", "year_between",
    "years_at_least", "years_between", "semantic_match",
}
DEGREE_RANK = {
    "无": 0, "中专": 1, "高中": 1, "大专": 2, "专科": 2,
    "本科": 3, "学士": 3, "硕士": 4, "研究生": 4, "博士": 5,
}
HARD_SCREENING_SEMANTIC_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["results"],
    "additionalProperties": False,
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["rule_id", "status", "reason", "source_quotes"],
                "properties": {
                    "rule_id": {"type": "string", "minLength": 1},
                    "status": {
                        "type": "string",
                        "enum": ["passed", "failed", "manual_review"],
                    },
                    "reason": {"type": "string", "minLength": 1},
                    "source_quotes": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                    },
                },
                # 自动通过或淘汰必须带原文证据；人工复核允许没有证据。
                "oneOf": [
                    {
                        "properties": {
                            "status": {"const": "passed"},
                            "source_quotes": {"minItems": 1},
                        }
                    },
                    {
                        "properties": {
                            "status": {"const": "failed"},
                            "source_quotes": {"minItems": 1},
                        }
                    },
                    {"properties": {"status": {"const": "manual_review"}}},
                ],
            },
        }
    },
}


def evaluate_hard_screening(
    *,
    application_id: str,
    resume_text: str,
    rules: list[dict[str, Any]],
    llm_config: dict[str, Any] | None = None,
    resume_profile: dict[str, Any] | None = None,
    candidate_facts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """对一组岗位硬条件返回通过、失败或待人工复核的逐条结论。"""
    facts = build_hard_screening_facts(resume_profile=resume_profile, candidate_facts=candidate_facts)
    source_facts = dict(
        candidate_facts
        or (resume_profile or {}).get("candidate_facts")
        or {}
    )
    normalized = [_normalize_rule(rule, index) for index, rule in enumerate(rules)]
    enabled = [rule for rule in normalized if rule["enabled"]]
    results = [
        _evaluate_deterministic(
            rule,
            resume_text,
            facts,
            resume_profile=resume_profile or {},
            source_facts=source_facts,
        )
        for rule in enabled
        if rule["operator"] != "semantic_match"
    ]
    semantic = [rule for rule in enabled if rule["operator"] == "semantic_match"]
    if semantic:
        results.extend(_evaluate_semantic(
            semantic,
            resume_text,
            llm_config or {},
            resume_profile=resume_profile or {},
            source_facts=source_facts,
        ))
    failed = [item for item in results if item["status"] == "failed"]
    review = [item for item in results if item["status"] == "manual_review"]
    status = "failed" if failed else "manual_review" if review else "passed"
    return {
        "application_id": application_id,
        "status": status,
        "rule_results": results,
        "summary": _summary(status, failed, review),
        "policy": {
            "rule_count": len(results),
            "failed_count": len(failed),
            "manual_review_count": len(review),
        },
        "schema_version": "hard_screening_result_v1",
    }


def _normalize_rule(rule: dict[str, Any], index: int) -> dict[str, Any]:
    scope = str(rule.get("source_scope") or rule.get("sourceScope") or "full_resume")
    operator = str(rule.get("operator") or "")
    if scope not in SUPPORTED_SCOPES:
        raise ValueError(f"unsupported_hard_screening_scope:{scope}")
    if operator not in SUPPORTED_OPERATORS:
        raise ValueError(f"unsupported_hard_screening_operator:{operator}")
    return {
        "rule_id": str(rule.get("rule_id") or rule.get("ruleId") or f"rule_{index + 1}"),
        "criterion_type": str(
            rule.get("criterion_type") or rule.get("criterionType") or ""
        ),
        "name": str(rule.get("name") or f"硬筛条件 {index + 1}"),
        "source_scope": scope,
        "operator": operator,
        "expected_value": rule.get("expected_value", rule.get("expectedValue")),
        "description": str(rule.get("description") or ""),
        "enabled": bool(rule.get("enabled", True)),
    }


def _evaluate_deterministic(
    rule: dict[str, Any],
    resume_text: str,
    facts: dict[str, Any],
    *,
    resume_profile: dict[str, Any],
    source_facts: dict[str, Any],
) -> dict[str, Any]:
    source = _scope_text(
        resume_text,
        rule["source_scope"],
        resume_profile=resume_profile,
        candidate_facts=source_facts,
    )
    operator, expected = rule["operator"], rule["expected_value"]
    structured_available = (
        operator == "degree_at_least" and bool(facts.get("highest_degree"))
    ) or (
        operator == "education_status_is"
        and bool(facts.get("highest_education_status"))
    ) or (
        operator == "year_between"
        and isinstance(
            facts.get("highest_education_graduation_year"), (int, float)
        )
    ) or (
        operator in {"years_at_least", "years_between"}
        and isinstance(facts.get("relevant_experience_years"), (int, float))
    )
    if rule["source_scope"] != "full_resume" and not source and not structured_available:
        return _result(
            rule,
            "manual_review",
            "未可靠识别到指定简历板块，不能据此自动拒绝。",
            [],
            reason_code="source_fact_missing",
        )
    if operator == "exists":
        passed = bool(source.strip())
        return _result(rule, "passed" if passed else "failed", "已找到对应简历板块。" if passed else "未找到对应简历板块。", [])
    if operator in {"contains_any", "contains_all", "not_contains_any"}:
        values = _string_list(expected)
        if not values:
            return _result(
                rule, "manual_review", "规则未填写有效要求值。", [],
                reason_code="policy_invalid",
            )
        normalized_source = _normalize_text(source)
        matched = [value for value in values if _normalize_text(value) in normalized_source]
        if operator == "contains_any":
            passed, reason = bool(matched), "命中至少一个指定条件。" if matched else "未命中任一指定条件。"
        elif operator == "contains_all":
            passed = len(matched) == len(values)
            reason = "已命中全部指定条件。" if passed else "未完整命中全部指定条件。"
        else:
            passed, reason = not matched, "未出现禁止项。" if not matched else "出现了禁止项。"
        quotes = [quote for quote in (_source_quote(source, value) for value in matched[:5]) if quote]
        return _result(rule, "passed" if passed else "failed", reason, quotes)
    if operator == "degree_at_least":
        expected_degree = str(expected or "")
        found_degree, found_rank = _highest_degree(str(facts.get("highest_degree") or "") or source)
        expected_rank = _degree_rank(expected_degree)
        if not expected_rank:
            return _result(
                rule, "manual_review", "硬筛规则中的学历要求无效。", [],
                reason_code="policy_invalid",
            )
        if not found_rank:
            return _result(
                rule, "manual_review", "无法可靠确定学历层级。", [],
                reason_code="source_fact_missing",
            )
        return _result(rule, "passed" if found_rank >= expected_rank else "failed", f"识别到最高学历为{found_degree}，要求至少为{expected_degree}。", [found_degree])
    if operator == "education_status_is":
        expected_status = _education_status(expected)
        found_status = _education_status(facts.get("highest_education_status"))
        if not expected_status:
            return _result(
                rule, "manual_review", "硬筛规则中的毕业状态无效。", [],
                reason_code="policy_invalid",
            )
        if not found_status:
            return _result(
                rule, "manual_review", "无法可靠确定最高学历的毕业状态。", [],
                reason_code="source_fact_missing",
            )
        passed = found_status == expected_status
        found_label = _education_status_label(found_status)
        expected_label = _education_status_label(expected_status)
        quote = str(facts.get("highest_education_source_text") or "").strip()
        return _result(
            rule,
            "passed" if passed else "failed",
            f"识别到最高学历状态为{found_label}，要求为{expected_label}。",
            [quote] if quote else [found_label],
        )
    if operator == "year_between":
        expected_range = expected if isinstance(expected, dict) else {}
        minimum = _number(expected_range.get("min"))
        maximum = _number(expected_range.get("max"))
        found_year = facts.get("highest_education_graduation_year")
        if minimum is None and maximum is None:
            return _result(
                rule, "manual_review", "硬筛规则中的毕业年份范围无效。", [],
                reason_code="policy_invalid",
            )
        if not isinstance(found_year, (int, float)):
            return _result(
                rule, "manual_review", "无法可靠确定最高学历的毕业年份。", [],
                reason_code="source_fact_missing",
            )
        passed = (
            (minimum is None or found_year >= minimum)
            and (maximum is None or found_year <= maximum)
        )
        requirement = _number_range_label(minimum, maximum, unit="年")
        return _result(
            rule,
            "passed" if passed else "failed",
            f"识别到最高学历毕业年份为{found_year:g}年，要求{requirement}。",
            [f"{found_year:g}"],
        )
    if operator in {"years_at_least", "years_between"}:
        found_years = facts.get("relevant_experience_years")
        found_years = found_years if isinstance(found_years, (int, float)) else _highest_years(source)
        if operator == "years_at_least":
            minimum, maximum = _number(expected), None
        else:
            expected_range = expected if isinstance(expected, dict) else {}
            minimum, maximum = _number(expected_range.get("min")), _number(expected_range.get("max"))
        if minimum is None and maximum is None:
            return _result(
                rule, "manual_review", "硬筛规则中的工作年限范围无效。", [],
                reason_code="policy_invalid",
            )
        if found_years is None:
            return _result(
                rule, "manual_review", "无法可靠确定相关年限。", [],
                reason_code="source_fact_missing",
            )
        passed = (minimum is None or found_years >= minimum) and (maximum is None or found_years <= maximum)
        requirement = _number_range_label(minimum, maximum, unit="年")
        return _result(rule, "passed" if passed else "failed", f"识别到年限约为{found_years:g}年，要求{requirement}。", [f"{found_years:g}年"])
    return _result(
        rule, "manual_review", "当前规则需要人工复核。", [],
        reason_code="policy_invalid",
    )


def _evaluate_semantic(
    rules: list[dict[str, Any]],
    resume_text: str,
    llm_config: dict[str, Any],
    *,
    resume_profile: dict[str, Any],
    source_facts: dict[str, Any],
) -> list[dict[str, Any]]:
    pending, results = [], []
    for rule in rules:
        source = _scope_text(
            resume_text,
            rule["source_scope"],
            resume_profile=resume_profile,
            candidate_facts=source_facts,
        )
        if rule["source_scope"] != "full_resume" and not source:
            results.append(_result(
                rule,
                "manual_review",
                "未可靠识别到指定简历板块，不能据此自动拒绝。",
                [],
                reason_code="source_fact_missing",
            ))
        else:
            pending.append({**rule, "source_text": source})
    if not pending:
        return results
    if not load_llm_settings(llm_config).is_enabled_for(WORKFLOW_NAME):
        return results + [
            _result(
                rule, "manual_review", "语义硬筛未启用，需要人工复核。", [],
                reason_code="model_configuration_required",
            )
            for rule in pending
        ]
    overrides = dict(llm_config)
    workflows = {key: dict(value) for key, value in (overrides.get("workflows") or {}).items() if isinstance(value, dict)}
    workflows[WORKFLOW_NAME] = {**workflows.get(WORKFLOW_NAME, {}), "json_mode": True, "strict_json_schema": False, "temperature": 0}
    overrides["workflows"] = workflows
    try:
        parsed, _ = call_json_llm(
            workflow_name=WORKFLOW_NAME,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "只依据简历原文判断硬性条件。每个输入rule_id必须恰好返回一次，"
                        "不得遗漏、重复或增加规则。信息不足返回manual_review。"
                        "对证书和语言等级按通用语义判断，例如CET-6、大学英语六级和"
                        "明确标注为英语等级的“六级”表示同一资格；但不得把普通技能描述推断为证书。"
                        "passed或failed必须提供至少一条source_quotes；source_quotes必须是"
                        "原文连续片段，不得改写或使用省略号。只返回JSON。"
                    ),
                },
                {"role": "user", "content": json.dumps({"rules": pending}, ensure_ascii=False)},
            ],
            schema_name="hard_screening_result_v1",
            settings_overrides=overrides,
            json_schema=HARD_SCREENING_SEMANTIC_RESPONSE_SCHEMA,
        )
    except Exception as exc:
        if getattr(exc, "retryable", False):
            raise
        parsed = None
    raw_results = (parsed or {}).get("results") if isinstance(parsed, dict) else None
    returned_items = list(raw_results) if isinstance(raw_results, list) else []
    expected_ids = [str(rule["rule_id"]) for rule in pending]
    returned_ids = [
        str(item.get("rule_id") or "")
        for item in returned_items
        if isinstance(item, dict)
    ]
    if (
        len(returned_items) != len(expected_ids)
        or len(returned_ids) != len(returned_items)
        or len(set(expected_ids)) != len(expected_ids)
        or len(set(returned_ids)) != len(returned_ids)
        or set(returned_ids) != set(expected_ids)
    ):
        return results + [
            _result(
                rule,
                "manual_review",
                "模型返回的规则结果存在缺失、重复或未知项，需要人工复核。",
                [],
                reason_code="model_result_invalid",
            )
            for rule in pending
        ]

    returned = {str(item["rule_id"]): item for item in returned_items}
    for rule in rules:
        if any(item["rule_id"] == rule["rule_id"] for item in results):
            continue
        item, source = returned.get(rule["rule_id"]), _scope_text(
            resume_text,
            rule["source_scope"],
            resume_profile=resume_profile,
            candidate_facts=source_facts,
        )
        if item is None:
            results.append(_result(
                rule, "manual_review", "语义判断未返回有效结果。", [],
                reason_code="model_result_invalid",
            ))
            continue
        quotes = [quote for quote in _string_list(item.get("source_quotes")) if quote in source and "..." not in quote and "……" not in quote]
        status, reason = str(item.get("status") or "manual_review"), str(item.get("reason") or "需要人工复核。")
        if status in {"passed", "failed"} and not quotes:
            status, reason = "manual_review", "模型未返回可在原文定位的证据，需要人工复核。"
        reason_code = (
            "model_result_invalid"
            if status == "manual_review" and not quotes and str(item.get("status")) in {"passed", "failed"}
            else "semantic_inconclusive" if status == "manual_review" else ""
        )
        results.append(_result(
            rule, status, reason, quotes, reason_code=reason_code
        ))
    return results


def _scope_text(
    resume_text: str,
    scope: str,
    *,
    resume_profile: dict[str, Any] | None = None,
    candidate_facts: dict[str, Any] | None = None,
) -> str:
    if scope == "full_resume":
        return resume_text
    # Non-full scopes are a strict ResumeProfile contract. Missing facts must
    # become manual review/recovery, never a second parser over raw Markdown.
    return _structured_scope_text(
        scope,
        resume_profile or {},
        candidate_facts or {},
    )


def _structured_scope_text(
    scope: str,
    resume_profile: dict[str, Any],
    candidate_facts: dict[str, Any],
) -> str:
    lines: list[str] = []
    if scope == "education":
        lines.extend(_record_text(item) for item in candidate_facts.get("education_records") or [])
    elif scope == "certifications":
        lines.extend(_record_text(item) for item in candidate_facts.get("qualification_records") or [])
    elif scope == "skills":
        for item in list(resume_profile.get("skill_claims") or []):
            if not isinstance(item, dict):
                continue
            quoted = _source_ref_text(item)
            if quoted:
                lines.append(quoted)
            else:
                values = [
                    str(item.get("skill_name") or "").strip(),
                    *[str(value).strip() for value in list(item.get("details") or [])],
                ]
                lines.append("；".join(value for value in values if value))
    elif scope in {"work_experience", "project_experience"}:
        for experience in list(resume_profile.get("experience_units") or []):
            if not isinstance(experience, dict):
                continue
            for item in [
                *list(experience.get("context_items") or []),
                *list(experience.get("work_units") or []),
            ]:
                if not isinstance(item, dict):
                    continue
                lines.append(
                    str(item.get("text") or item.get("raw_text") or "").strip()
                    or _source_ref_text(item)
                )
    return "\n".join(dict.fromkeys(line for line in lines if line)).strip()


def _record_text(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    return str(value.get("raw_text") or "").strip() or _source_ref_text(value)


def _source_ref_text(value: dict[str, Any]) -> str:
    return "；".join(
        str(item.get("quote") or "").strip()
        for item in list(value.get("source_refs") or [])
        if isinstance(item, dict) and str(item.get("quote") or "").strip()
    )


def _result(
    rule: dict[str, Any],
    status: str,
    reason: str,
    quotes: list[str],
    *,
    reason_code: str = "",
) -> dict[str, Any]:
    return {
        **{
            key: rule[key]
            for key in (
                "rule_id",
                "criterion_type",
                "name",
                "source_scope",
                "operator",
                "expected_value",
            )
        },
        "status": status,
        "reason": reason,
        "reason_code": reason_code or None,
        "source_quotes": quotes,
    }


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", "", value).lower()


def _source_quote(source: str, expected: str) -> str:
    match = re.search(re.escape(expected.strip()), source, flags=re.IGNORECASE)
    return match.group(0) if match else ""


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in re.split(r"[,，]", str(value or "")) if item.strip()]


def _degree_rank(value: str) -> int:
    return max((rank for name, rank in DEGREE_RANK.items() if name in value), default=0)


def _highest_degree(text: str) -> tuple[str, int]:
    matches = [(name, rank) for name, rank in DEGREE_RANK.items() if name != "无" and name in text]
    return max(matches, key=lambda item: item[1]) if matches else ("", 0)


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _education_status(value: Any) -> str:
    normalized = re.sub(r"\s+", "", str(value or "")).casefold()
    if normalized in {"completed", "graduated", "已毕业", "毕业"}:
        return "completed"
    if normalized in {"in_progress", "studying", "在读", "预计毕业"}:
        return "in_progress"
    return ""


def _education_status_label(value: str) -> str:
    return {"completed": "已毕业", "in_progress": "在读"}.get(value, "未知")


def _number_range_label(
    minimum: float | None, maximum: float | None, *, unit: str
) -> str:
    if minimum is not None and maximum is None:
        return f"至少为{minimum:g}{unit}"
    if maximum is not None and minimum is None:
        return f"至多为{maximum:g}{unit}"
    return f"范围为{minimum:g}～{maximum:g}{unit}"


def _highest_years(text: str) -> float | None:
    values = [float(value) for value in re.findall(r"(?<!\d)(\d+(?:\.\d+)?)\s*年", text) if float(value) <= 60]
    return max(values) if values else None


def _summary(status: str, failed: list[dict[str, Any]], review: list[dict[str, Any]]) -> str:
    if status == "failed":
        return "未通过：" + "；".join(item["name"] for item in failed)
    if status == "manual_review":
        return "需要人工复核：" + "；".join(item["name"] for item in review)
    return "全部已启用的硬性条件均通过。"
