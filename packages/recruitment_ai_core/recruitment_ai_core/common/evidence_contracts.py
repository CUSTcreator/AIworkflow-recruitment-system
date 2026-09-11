from __future__ import annotations

from typing import Any, Literal


EvidenceEffect = Literal["supports", "extends", "clarifies", "contradicts", "irrelevant"]


def effect_from_evidence_unit(unit: dict[str, Any]) -> EvidenceEffect:
    verification = unit.get("verification_result")
    polarity = unit.get("polarity")
    if verification == "not_asked":
        return "irrelevant"
    if verification == "contradicted" or polarity == "negative" or unit.get("contradicts_screening"):
        return "contradicts"
    if verification == "verified" and unit.get("new_evidence"):
        return "extends"
    if verification in {"verified", "partially_verified"} or polarity == "positive":
        return "supports"
    if verification == "unclear":
        return "clarifies"
    return "irrelevant"


def evidence_record_from_unit(unit: dict[str, Any], *, application_id: str, stage: str) -> dict[str, Any]:
    evidence_id = unit.get("evidence_record_id") or unit.get("evidence_unit_id") or f"EV_{stage}_{application_id}"
    return {
        "evidence_id": evidence_id,
        "application_id": application_id,
        "stage": stage,
        "source_type": unit.get("source_type", "unknown"),
        "source_ref": {
            "question_id": unit.get("source_question_id"),
            "response_id": unit.get("source_response_id"),
            "note_id": unit.get("source_note_id"),
            "draft_item_id": unit.get("source_draft_item_id"),
        },
        "raw_text": unit.get("raw_text", ""),
        "evidence_summary": unit.get("evidence_summary", ""),
        "effect": effect_from_evidence_unit(unit),
        "polarity": unit.get("polarity", "neutral"),
        "target_job_capability_ids": list(unit.get("target_job_capability_ids", [])),
        "target_preset_indicator_ids": list(unit.get("target_preset_indicator_ids", [])),
        "related_risk_ids": list(unit.get("risk_ids", unit.get("related_risk_ids", []))),
        "related_interview_target_ids": list(unit.get("interview_target_ids", unit.get("related_interview_target_ids", []))),
        "status": "active",
        "metadata": {"source_evidence_unit_id": unit.get("evidence_unit_id")},
    }


def evidence_records_from_units(units: list[dict[str, Any]], *, application_id: str, stage: str) -> list[dict[str, Any]]:
    return [evidence_record_from_unit(unit, application_id=application_id, stage=stage) for unit in units]
