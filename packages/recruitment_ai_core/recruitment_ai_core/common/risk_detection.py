from __future__ import annotations

import hashlib
import json
from typing import Any

from recruitment_ai_core.llm import call_json_llm, load_llm_settings
from recruitment_ai_core.screening_scoring.profile_evidence import education_records


WORKFLOW_NAME = "job_capability"
RISK_TYPES = [
    "fact_conflict",
    "ownership_conflict",
    "result_conflict",
    "timeline_conflict",
    "authenticity_conflict",
]


def detect_initial_risks(
    *,
    application_id: str,
    scoring_evidence: dict[str, Any],
    resume_profile: dict[str, Any],
    llm_config: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Detect explicit contradictions only; missing or weak evidence is never a Risk."""
    facts = _facts(scoring_evidence, resume_profile)
    if len(facts) < 2:
        return [], []
    settings = load_llm_settings(llm_config)
    if not settings.is_enabled_for(WORKFLOW_NAME):
        return [], []
    schema = _schema()
    traces: list[dict[str, Any]] = []
    validation_error = ""
    for attempt in range(2):
        try:
            payload, trace = call_json_llm(
                workflow_name=WORKFLOW_NAME,
                messages=[
                    {"role": "system", "content": _prompt()},
                    {"role": "user", "content": json.dumps({
                        "candidate_facts": facts,
                        "previous_validation_error": validation_error,
                    }, ensure_ascii=False)},
                ],
                schema_name="candidate_initial_risk_detection_v1",
                settings_overrides=llm_config,
                json_schema=schema,
            )
            traces.append({**trace, "stage": "initial_risk_detection", "attempt": attempt + 1})
            risks = _normalize(application_id, payload.get("risks", []) if payload else [], facts)
            return risks, traces
        except Exception as exc:
            validation_error = f"{type(exc).__name__}:{str(exc)[:200]}"
            traces.append({"stage": "initial_risk_detection", "attempt": attempt + 1, "status": "failed", "error": validation_error})
    if not settings.fail_open:
        raise RuntimeError(f"initial_risk_detection_failed:{validation_error}")
    return [], traces


def _facts(snapshot: dict[str, Any], resume_profile: dict[str, Any]) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    for item in snapshot.get("work_units", []):
        if not item.get("is_current", True):
            continue
        evidence_id = str(item.get("work_unit_version_id") or item.get("work_unit_id") or "")
        facts.append({
            "evidence_id": evidence_id,
            "evidence_type": "work_unit",
            "text": item.get("raw_text") or " ".join(ref.get("quote", "") for ref in item.get("source_refs", [])),
            "target_refs": [item.get("work_unit_id"), item.get("project_id")],
        })
    for item in snapshot.get("skill_claims", []):
        evidence_id = str(item.get("skill_claim_id") or "")
        facts.append({
            "evidence_id": evidence_id,
            "evidence_type": "skill_claim",
            "text": item.get("raw_text") or item.get("claim_text") or item.get("name") or "",
            "target_refs": [evidence_id],
        })
    for index, item in enumerate(education_records(resume_profile), start=1):
        evidence_id = str(item.get("education_record_id") or f"EDU_{index:03d}")
        facts.append({
            "evidence_id": evidence_id,
            "evidence_type": "education_fact",
            "text": item.get("raw_text") or item.get("text") or "",
            "target_refs": [evidence_id],
        })
    return [item for item in facts if item["evidence_id"] and item["text"]]


def _normalize(application_id: str, rows: list[dict[str, Any]], facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    known_ids = {item["evidence_id"] for item in facts}
    valid_targets = {str(value) for item in facts for value in item.get("target_refs", []) if value}
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        refs = list(dict.fromkeys(str(item) for item in row.get("source_evidence_refs", []) if item))
        targets = list(dict.fromkeys(str(item) for item in row.get("target_refs", []) if item))
        if row.get("risk_type") not in RISK_TYPES or len(refs) < 2 or not set(refs) <= known_ids:
            raise ValueError("initial_risk_sources_invalid")
        if not set(targets) <= valid_targets:
            raise ValueError("initial_risk_targets_invalid")
        key = _hash(row["risk_type"], *sorted(refs))
        if key in seen:
            continue
        seen.add(key)
        output.append({
            "risk_id": f"RISK_{application_id}_{key[:16]}",
            "risk_type": row["risk_type"],
            "summary": row["summary"],
            "source_evidence_refs": refs,
            "target_refs": targets,
            "verification_need": row["verification_need"],
            "status": "open",
        })
    return output


def _prompt() -> str:
    return (
        "只识别会导致评估失真的明确事实冲突，包括事实陈述、责任归属、结果、时间线或真实性冲突。"
        "能力弱、信息缺失、证明较少、表述不够详细、技术栈不同和主观怀疑不得生成Risk。"
        "每个Risk必须引用至少两个相互冲突且来自输入的evidence_id；没有明确冲突时返回空数组。"
        "target_refs只能选择输入事实中的target_refs。只返回符合JSON Schema的对象。"
    )


def _schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["risks"],
        "additionalProperties": False,
        "properties": {
            "risks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["risk_type", "summary", "source_evidence_refs", "target_refs", "verification_need"],
                    "additionalProperties": False,
                    "properties": {
                        "risk_type": {"type": "string", "enum": RISK_TYPES},
                        "summary": {"type": "string", "maxLength": 300},
                        "source_evidence_refs": {"type": "array", "minItems": 2, "items": {"type": "string"}},
                        "target_refs": {"type": "array", "items": {"type": "string"}},
                        "verification_need": {"type": "string", "maxLength": 300},
                    },
                },
            }
        },
    }


def _hash(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
