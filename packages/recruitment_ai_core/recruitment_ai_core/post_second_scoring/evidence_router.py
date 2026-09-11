from __future__ import annotations

from typing import Any

from recruitment_ai_core.common.evidence_contracts import evidence_records_from_units


def build_second_interview_evidence_records(input_data, evidence_units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return evidence_records_from_units(
        evidence_units, application_id=input_data.application_id, stage="second_interview"
    )


def route_second_interview_evidence(previous_profile: dict[str, Any], evidence_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "evidence_id": record["evidence_id"],
            "effect": record["effect"],
            "affected_job_capability_ids": list(record.get("target_job_capability_ids", [])),
            "affected_preset_indicator_ids": list(record.get("target_preset_indicator_ids", [])),
            "affected_risk_ids": list(record.get("related_risk_ids", [])),
            "affected_interview_target_ids": list(record.get("related_interview_target_ids", [])),
        }
        for record in evidence_records
    ]
