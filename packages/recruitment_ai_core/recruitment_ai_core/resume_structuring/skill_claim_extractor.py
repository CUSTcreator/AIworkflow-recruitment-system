from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

from recruitment_ai_core.llm import call_json_llm, load_llm_settings
from recruitment_ai_core.screening_scoring.contracts import VerifiedResumeIR


WORKFLOW_NAME = "resume_skill_claim_extraction"


def extract_skill_claims(
    resume_ir: VerifiedResumeIR,
    *,
    llm_config: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]] | None, list[dict[str, Any]], str | None]:
    statements = _skill_statement_inputs(resume_ir)
    if not statements:
        return [], [], None
    settings = load_llm_settings(llm_config)
    if not settings.is_enabled_for(WORKFLOW_NAME):
        return None, [settings.trace(WORKFLOW_NAME)], "skill_claim_llm_disabled"
    error = ""
    traces: list[dict[str, Any]] = []
    for attempt in range(1):
        try:
            payload, trace = call_json_llm(
                workflow_name=WORKFLOW_NAME,
                messages=[
                    {"role": "system", "content": _prompt()},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "skill_statements": statements,
                                "previous_validation_error": error,
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
                schema_name="resume_skill_claim_extraction_v1",
                settings_overrides=llm_config,
                json_schema=_schema(),
            )
            traces.append({**trace, "attempt": attempt + 1})
            # 来源校验按明细宽容处理：删除无法回溯的 detail 或空 Claim，
            # 其余已验证结果继续发布，不能因一个模型扩写丢掉整份技能声明。
            payload, dropped_details = _drop_unverifiable_claim_details(
                payload, statements
            )
            if dropped_details:
                traces[-1]["dropped_unverifiable_claim_details"] = dropped_details
            valid, error = _validate(payload, statements)
            if valid:
                return _normalize(payload, statements), traces, None
        except Exception as exc:
            if hasattr(exc, "retryable"):
                raise
            trace = dict(getattr(exc, "llm_trace", {}) or {})
            error = f"{type(exc).__name__}:{str(exc)[:200]}"
            traces.append({**trace, "attempt": attempt + 1, "error": error})
    return None, traces, error or "skill_claim_response_invalid"


def _prompt() -> str:
    return (
        "只处理输入中的专业技能原文。每个SkillStatement可拆成0至多个能被岗位独立要求、"
        "独立判级的SkillClaim；同一技术或生态下的知识点、组件和机制放入details，不逐词拆分。"
        "不得推断使用经历或熟练程度。证书、语言等级、年限以及沟通、责任心、团队合作等软技能"
        "不生成SkillClaim。对于个人特长或自我评价，只提取其中明确陈述的软件、工具、编程、"
        "仿真、算法或专业技术能力。"
        "必须完整返回输入中的每个statement_id且每个只能返回一次，不得遗漏、重复或新增。"
        "skill_name使用简短能力主题；details只写原文明确出现的内容。没有可独立判级的内容时claims返回空数组。"
        "只输出Schema规定的字段，不要输出解释、评分或其他字段。"
    )


def _schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["statements"],
        "additionalProperties": False,
        "properties": {
            "statements": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "required": ["statement_id", "claims"],
                    "additionalProperties": False,
                    "properties": {
                        "statement_id": {"type": "string"},
                        "claims": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "required": ["skill_name", "details"],
                                "additionalProperties": False,
                                "properties": {
                                    "skill_name": {"type": "string", "minLength": 1},
                                    "details": {
                                        "type": "array",
                                        "minItems": 1,
                                        "items": {"type": "string", "minLength": 1},
                                    },
                                },
                            },
                        },
                    },
                },
            }
        },
    }


def _validate(
    payload: dict[str, Any] | None,
    statements: list[dict[str, Any]],
) -> tuple[bool, str]:
    if not isinstance(payload, dict) or not isinstance(payload.get("statements"), list):
        return False, "statements_missing"
    if any(not isinstance(item, dict) for item in payload["statements"]):
        return False, "statement_invalid"
    expected = {item["skill_statement_id"] for item in statements}
    actual = [str(item.get("statement_id") or "") for item in payload["statements"]]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        return False, "statement_cartesian_mismatch"
    for item in payload["statements"]:
        claims = item.get("claims")
        if not isinstance(claims, list):
            return False, "claims_invalid"
        if any(not isinstance(claim, dict) for claim in claims):
            return False, "claim_invalid"
        names = [str(claim.get("skill_name") or "").strip() for claim in claims]
        if any(not name for name in names) or len(names) != len(set(names)):
            return False, "claim_name_invalid_or_duplicate"
        source_text = next(
            row["source_ref"]["quote"]
            for row in statements
            if row["skill_statement_id"] == item["statement_id"]
        )
        normalized_source = _normalize_for_source_check(source_text)
        for claim in claims:
            details = claim.get("details")
            if not isinstance(details, list) or not details:
                return False, "claim_details_invalid"
            for detail in details:
                normalized_detail = _normalize_for_source_check(str(detail))
                if not normalized_detail or normalized_detail not in normalized_source:
                    return False, "claim_detail_not_in_source"
    return True, ""


def _normalize_for_source_check(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _drop_unverifiable_claim_details(
    payload: dict[str, Any] | None,
    statements: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, int]:
    """删除无法回溯的技能明细，保留同批次中其余有效Claim。"""
    if not isinstance(payload, dict) or not isinstance(payload.get("statements"), list):
        return payload, 0
    source_by_id = {
        item["skill_statement_id"]: _normalize_for_source_check(
            item["source_ref"]["quote"]
        )
        for item in statements
    }
    sanitized_statements: list[dict[str, Any]] = []
    dropped_details = 0
    for statement in payload["statements"]:
        if not isinstance(statement, dict):
            return payload, 0
        statement_id = str(statement.get("statement_id") or "")
        claims = statement.get("claims")
        source_text = source_by_id.get(statement_id)
        if source_text is None or not isinstance(claims, list):
            return payload, 0
        sanitized_claims: list[dict[str, Any]] = []
        seen_names: set[str] = set()
        for claim in claims:
            if not isinstance(claim, dict) or not isinstance(claim.get("details"), list):
                return payload, 0
            skill_name = str(claim.get("skill_name") or "").strip()
            verified_details = [
                str(detail).strip()
                for detail in claim["details"]
                if _normalize_for_source_check(str(detail))
                and _normalize_for_source_check(str(detail)) in source_text
            ]
            dropped_details += len(claim["details"]) - len(verified_details)
            if not skill_name or not verified_details or skill_name in seen_names:
                continue
            seen_names.add(skill_name)
            sanitized_claims.append({
                "skill_name": skill_name,
                "details": verified_details,
            })
        sanitized_statements.append({
            "statement_id": statement_id,
            "claims": sanitized_claims,
        })
    return {"statements": sanitized_statements}, dropped_details


def _normalize(
    payload: dict[str, Any],
    statements: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    source_by_id = {
        item["skill_statement_id"]: item["source_ref"] for item in statements
    }
    output: list[dict[str, Any]] = []
    for statement in payload["statements"]:
        statement_id = str(statement["statement_id"])
        claims = []
        for index, claim in enumerate(statement["claims"], start=1):
            claims.append(
                {
                    "skill_claim_id": f"SC_{statement_id}_{index:02d}",
                    "skill_name": str(claim["skill_name"]).strip(),
                    "details": list(dict.fromkeys(
                        str(item).strip()
                        for item in claim.get("details", [])
                        if str(item).strip()
                    )),
                    "source_refs": [dict(source_by_id[statement_id])],
                }
            )
        output.append(
            {
                "skill_statement_id": statement_id,
                "source_ref": dict(source_by_id[statement_id]),
                "skill_claims": claims,
            }
        )
    return output


def _is_skill_heading(text: str) -> bool:
    return "".join(text.split()).rstrip("：:") in {
        "专业技能", "技能清单", "技能", "技术能力", "软件能力", "工具能力",
        "专业能力", "计算机技能", "软件技能", "熟悉工具",
    }


def _skill_section_label(resume_ir: VerifiedResumeIR, span: Any) -> str:
    """恢复技能原文所在的栏目标题，别名栏目不再统一伪装成“专业技能”。"""
    preceding = [
        item
        for item in resume_ir.candidate_spans
        if (
            item.section == "skills"
            and item.source_line_start <= span.source_line_start
            and _is_skill_heading(item.text)
        )
    ]
    if preceding:
        return preceding[-1].text.strip().rstrip("：:") or "技能"
    return "技能"


def _skill_statement_inputs(resume_ir: VerifiedResumeIR) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for span in resume_ir.candidate_spans:
        explicit_skill = span.section == "skills" and not _is_skill_heading(span.text)
        profile_skill = span.section == "profile" and _is_profile_skill_candidate(span.text)
        if not explicit_skill and not profile_skill:
            continue
        section_label = (
            _profile_field_label(span.text)
            if profile_skill
            else _skill_section_label(resume_ir, span)
        )
        output.append({
            "skill_statement_id": span.span_id,
            "source_ref": {
                "section": section_label,
                "quote": span.text,
                "block_id": span.source_block_ids[0] if span.source_block_ids else "",
            },
        })
    return output


def build_degraded_skill_statements(
    resume_ir: VerifiedResumeIR,
) -> list[dict[str, Any]]:
    """Keep explicit, source-backed skill text when semantic splitting fails."""
    output: list[dict[str, Any]] = []
    for statement in _skill_statement_inputs(resume_ir):
        quote = str(statement["source_ref"]["quote"])
        profile_source = _is_profile_skill_candidate(quote)
        label, separator, remainder = quote.partition("：")
        if not separator:
            label, separator, remainder = quote.partition(":")
        details = _technical_clauses(quote) if profile_source else [quote]
        if not profile_source and separator and remainder.strip():
            details = [remainder.strip()]
        if not details:
            continue
        skill_name = (
            label.strip()
            if not profile_source and separator and 1 <= len(label.strip()) <= 24
            else "专业技能"
        )
        source_ref = dict(statement["source_ref"])
        output.append({
            "skill_statement_id": statement["skill_statement_id"],
            "source_ref": source_ref,
            "skill_claims": [{
                "skill_claim_id": f"SC_{statement['skill_statement_id']}_01",
                "skill_name": skill_name,
                "details": details,
                "source_refs": [source_ref],
            }],
        })
    return output


def _is_profile_skill_candidate(text: str) -> bool:
    label = _profile_field_label(text)
    if label not in {"个人特长/自我评价", "个人特长", "自我评价", "个人评价"}:
        return False
    return bool(_technical_clauses(text))


def _profile_field_label(text: str) -> str:
    match = re.match(
        r"^\s*(个人特长/自我评价|个人特长|自我评价|个人评价)\s*[：:]?",
        text,
    )
    return match.group(1) if match else "技能"


def _technical_clauses(text: str) -> list[str]:
    """Select technical assertions by sentence meaning, not product names."""
    value = re.sub(
        r"^\s*(?:个人特长/自我评价|个人特长|自我评价|个人评价)\s*[：:]?\s*",
        "",
        text,
    )
    clauses = [
        item.strip()
        for item in re.split(r"[；;。\n，,]", value)
        if item.strip()
    ]
    ability = re.compile(r"(?:熟练|熟悉|掌握|能够|可使用|会使用|具备|擅长|运用|应用)")
    technical_domain = re.compile(
        r"(?:软件|工具|程序|编程|代码|开发|数据库|算法|模型|仿真|计算机|机器学习|"
        r"人工智能|AI|设计|分析|技术|力学|工程)"
    )
    return [
        clause
        for clause in clauses
        if ability.search(clause) and technical_domain.search(clause)
    ]
