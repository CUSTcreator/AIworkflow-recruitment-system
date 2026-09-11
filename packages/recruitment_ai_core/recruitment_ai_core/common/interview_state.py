from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

from recruitment_ai_core.llm import call_json_llm, load_llm_settings


RISK_CATEGORIES = {
    "fact_conflict", "ownership_conflict", "result_conflict",
    "timeline_conflict", "authenticity_conflict",
}


def update_interview_state(
    *,
    profile: dict[str, Any],
    parse_result: dict[str, Any],
    stage: str,
    existing_risks: list[dict[str, Any]] | None = None,
    existing_targets: list[dict[str, Any]] | None = None,
    llm_config: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """仅维护 InterviewTarget 的跨阶段核验状态。

    ``existing_risks`` 保留为旧工作流输入兼容参数，但不会再读取、生成或返回。
    """
    del profile, existing_risks
    targets = deepcopy(existing_targets or [])
    open_targets = [item for item in targets if _status(item) not in {"resolved", "closed"}]
    decision = _try_decide([], open_targets, parse_result, stage, llm_config)
    target_changes: list[dict[str, Any]] = []
    if decision:
        target_changes = _apply_target_results(targets, decision["target_results"], stage)
    return [], targets, [], target_changes, []

def _try_decide(
    risks: list[dict[str, Any]],
    targets: list[dict[str, Any]],
    parse_result: dict[str, Any],
    stage: str,
    config: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not risks and not targets and not _conflict_candidates(parse_result):
        return None
    workflow = "post_first_scoring" if stage == "after_first_interview" else "post_second_scoring"
    settings = load_llm_settings(config)
    if not settings.is_enabled_for(workflow):
        return None
    schema = _schema([_risk_id(item) for item in risks], [_target_id(item) for item in targets])
    call_config = _json_mode_config(config, workflow)
    validation_error = ""
    for _ in range(2):
        try:
            payload, _trace = call_json_llm(
                workflow_name=workflow,
                messages=_messages(risks, targets, parse_result, validation_error),
                schema_name="interview_state_update_v1",
                settings_overrides=call_config,
                json_schema=schema,
            )
            if payload is None:
                return None
            return _validate(payload, risks, targets, parse_result)
        except Exception as exc:
            validation_error = f"{type(exc).__name__}:{str(exc)[:200]}"
    if not settings.fail_open:
        raise RuntimeError(f"interview_state_update_failed:{validation_error}")
    return None


def _messages(
    risks: list[dict[str, Any]], targets: list[dict[str, Any]],
    parse_result: dict[str, Any], error: str,
) -> list[dict[str, str]]:
    system = (
        "你只判断面试后的Risk和InterviewTarget状态，不评分。"
        "只使用已经通过校验的统一 assertions 原文证据。"
        "Risk只有原事实冲突被明确解释或纠正时才能resolved；回答相关问题或表现良好不能单独关闭Risk。"
        "InterviewTarget只有所需事实或现场观察已经实际获得时才能resolved，否则填写remaining_need。"
        "只有发现新的事实、责任、结果、时间线或真实性冲突时才能生成new_risks。不得新增能力。"
    )
    body = {
        "open_risks": risks,
        "open_interview_targets": targets,
        "interview_parse_result": parse_result,
        "previous_validation_error": error,
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(body, ensure_ascii=False)},
    ]


def _schema(risk_ids: list[str], target_ids: list[str]) -> dict[str, Any]:
    result = lambda id_name, ids, extra: {
        "type": "object", "additionalProperties": False,
        "required": [id_name, "resolved", "reason", *extra],
        "properties": {
            id_name: ({"type": "string", "enum": ids} if ids else {"type": "string"}),
            "resolved": {"type": "boolean"},
            "reason": {"type": "string", "maxLength": 300},
            **({"remaining_need": {"type": ["string", "null"], "maxLength": 300}} if extra else {}),
        },
    }
    source_ref = {
        "type": "object", "additionalProperties": False,
        "required": ["segment_id", "quote"],
        "properties": {"segment_id": {"type": "string"}, "quote": {"type": "string"}},
    }
    return {
        "type": "object", "additionalProperties": False,
        "required": ["risk_results", "target_results", "new_risks"],
        "properties": {
            "risk_results": {"type": "array", "items": result("risk_id", risk_ids, [])},
            "target_results": {"type": "array", "items": result("interview_target_id", target_ids, [])},
            "new_risks": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "required": ["risk_type", "summary", "source_refs", "target_refs", "verification_need"],
                "properties": {
                    "risk_type": {"type": "string", "enum": sorted(RISK_CATEGORIES)},
                    "summary": {"type": "string", "maxLength": 300},
                    "source_refs": {"type": "array", "minItems": 2, "items": source_ref},
                    "target_refs": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                    "verification_need": {"type": "string", "maxLength": 300},
                },
            }},
        },
    }


def _validate(
    payload: dict[str, Any], risks: list[dict[str, Any]], targets: list[dict[str, Any]],
    parse_result: dict[str, Any],
) -> dict[str, Any]:
    risk_ids = {_risk_id(item) for item in risks}
    target_ids = {_target_id(item) for item in targets}
    returned_risks = [item.get("risk_id") for item in payload.get("risk_results", [])]
    returned_targets = [item.get("interview_target_id") for item in payload.get("target_results", [])]
    if set(returned_risks) != risk_ids or len(returned_risks) != len(risk_ids):
        raise ValueError("risk_ids_incomplete")
    if set(returned_targets) != target_ids or len(returned_targets) != len(target_ids):
        raise ValueError("target_ids_incomplete")
    known_refs = {
        (str(ref.get("segment_id") or ""), str(ref.get("quote") or ""))
        for item in parse_result.get("assertions", []) or ()
        for ref in item.get("sourceRefs", item.get("source_refs", [])) or ()
        if isinstance(ref, dict)
    }
    valid_targets = _valid_target_refs(parse_result)
    for item in payload.get("new_risks", []):
        refs = {(ref.get("segment_id"), ref.get("quote")) for ref in item.get("source_refs", [])}
        if item.get("risk_type") not in RISK_CATEGORIES or len(refs) < 2 or not refs <= known_refs:
            raise ValueError("new_risk_source_invalid")
        if not set(item.get("target_refs", [])) <= valid_targets:
            raise ValueError("new_risk_target_invalid")
    return payload


def _apply_risk_results(
    risks: list[dict[str, Any]], results: list[dict[str, Any]], stage: str,
) -> list[dict[str, Any]]:
    by_id = {item["risk_id"]: item for item in results}
    changes = []
    for risk in risks:
        risk_id = _risk_id(risk)
        decision = by_id.get(risk_id)
        if not decision:
            continue
        before = _status(risk)
        after = "resolved" if decision["resolved"] else before
        risk["status"] = after
        risk["resolution_note"] = decision["reason"] if decision["resolved"] else risk.get("resolution_note", "")
        risk["last_updated_stage"] = stage
        if after != before:
            changes.append({"risk_id": risk_id, "previous_status": before, "new_status": after, "reason": decision["reason"]})
    return changes


def _apply_target_results(
    targets: list[dict[str, Any]], results: list[dict[str, Any]], stage: str,
) -> list[dict[str, Any]]:
    by_id = {item["interview_target_id"]: item for item in results}
    changes = []
    for target in targets:
        target_id = _target_id(target)
        decision = by_id.get(target_id)
        if not decision:
            continue
        before = _status(target)
        after = "resolved" if decision["resolved"] else "open"
        target.update({
            "status": after,
            "result": decision["reason"],
            "last_updated_stage": stage,
        })
        if after != before:
            changes.append({
                "interview_target_id": target_id,
                "previous_status": before,
                "new_status": after,
                "reason": decision["reason"],
            })
    return changes


def _append_new_risks(
    risks: list[dict[str, Any]], rows: list[dict[str, Any]],
    parse_result: dict[str, Any], stage: str,
) -> list[dict[str, Any]]:
    changes = []
    existing_keys = {_risk_key(item) for item in risks}
    for row in rows:
        key = _risk_key(row)
        if key in existing_keys:
            continue
        existing_keys.add(key)
        risk_id = _stable_id("RISK", parse_result.get("record_id", ""), key)
        risks.append({
            "risk_id": risk_id,
            "risk_type": row["risk_type"],
            "summary": row["summary"],
            "source_evidence_refs": row["source_refs"],
            "target_refs": row["target_refs"],
            "verification_need": row["verification_need"],
            "status": "open",
        })
        changes.append({"risk_id": risk_id, "change_type": "added", "new_status": "open"})
    return changes


def _append_parse_conflict_risks(
    risks: list[dict[str, Any]], parse_result: dict[str, Any], stage: str,
) -> list[dict[str, Any]]:
    """Turn contradictory same-round corrections into deterministic review risks."""
    changes: list[dict[str, Any]] = []
    existing_ids = {_risk_id(item) for item in risks}
    round_id = str(
        parse_result.get("interview_round_id")
        or parse_result.get("record_id")
        or ""
    )
    for conflict in parse_result.get("parse_conflicts", []):
        target_id = str(conflict.get("target_id") or "")
        conflict_type = str(conflict.get("conflict_type") or "interview_update")
        risk_id = _stable_id(
            "RISK", round_id, "parse_conflict", conflict_type, target_id
        )
        if risk_id in existing_ids:
            continue
        existing_ids.add(risk_id)
        title = f"同轮面评对 {target_id or '同一事实'} 存在互相冲突的修改"
        risks.append({
            "risk_id": risk_id,
            "risk_type": "fact_conflict",
            "summary": title,
            "source_evidence_refs": list(conflict.get("source_refs", [])),
            "target_refs": [target_id] if target_id else [],
            "verification_need": "人工确认本轮面评中哪一条修改有效",
            "status": "open",
        })
        changes.append({
            "risk_id": risk_id,
            "change_type": "added",
            "new_status": "open",
        })
    return changes


def _ensure_targets_for_open_state(
    profile: dict[str, Any], risks: list[dict[str, Any]], targets: list[dict[str, Any]],
    stage: str, changes: list[dict[str, Any]],
) -> None:
    active_risk_ids = {
        (_target_risk_id(item)) for item in targets
        if _status(item) not in {"resolved", "closed"} and _target_risk_id(item)
    }
    for risk in risks:
        risk_id = _risk_id(risk)
        if _status(risk) in {"resolved", "closed"} or risk_id in active_risk_ids:
            continue
        target = _new_target(
            purpose="resolve_risk", target_type="job_capability", target_ids=list(risk.get("target_refs", [])),
            risk_ids=[risk_id], remaining_need=risk.get("verification_need") or risk.get("summary") or risk_id,
            stage=stage,
        )
        targets.append(target)
        changes.append({"interview_target_id": target["interview_target_id"], "change_type": "added", "new_status": "open"})


def _new_target(
    *, purpose: str, target_type: str, target_ids: list[str], risk_ids: list[str],
    remaining_need: str, stage: str,
) -> dict[str, Any]:
    target_id = _stable_id("IT", purpose, target_type, *sorted(target_ids), *sorted(risk_ids))
    return {
        "interview_target_id": target_id,
        "purpose": purpose, "target_type": target_type,
        "expected_result_type": "interview_performance" if purpose == "direct_demonstration" else "experience_fact",
        "target_ids": target_ids, "risk_ids": risk_ids,
        "source_evidence_ids": [],
        "status": "open", "remaining_need": remaining_need,
        "last_updated_stage": stage,
    }


def _conflict_candidates(parse_result: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item for item in parse_result.get("assertions", [])
        if isinstance(item, dict) and item.get("isExperienceRelated")
    ]


def _valid_target_refs(parse_result: dict[str, Any]) -> set[str]:
    refs: set[str] = set()
    for item in parse_result.get("assertions", []) or ():
        if isinstance(item, dict):
            refs.update(str(value) for value in item.get("candidateAnchorIds", []) or () if value)
    return refs


def _risk_id(item: dict[str, Any]) -> str:
    return str(item.get("risk_id") or "")


def _target_id(item: dict[str, Any]) -> str:
    return str(item.get("interview_target_id") or "")


def _target_risk_id(item: dict[str, Any]) -> str:
    values = item.get("risk_ids") or []
    return str(values[0] if values else "")


def _status(item: dict[str, Any]) -> str:
    return str(item.get("status") or "open")


def _risk_key(item: dict[str, Any]) -> str:
    category = str(item.get("risk_type") or "")
    targets = sorted(str(value) for value in item.get("target_refs", []) if value)
    summary = str(item.get("summary") or "")
    return "|".join([category, *targets, summary])


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


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
