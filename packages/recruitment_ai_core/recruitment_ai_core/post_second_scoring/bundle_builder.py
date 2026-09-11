from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .policy import BUNDLE_SCHEMA_VERSION, LLM_SCHEMA_VERSION, MATH_CONTRACT_VERSION, POLICY_VERSION


def build_post_second_bundle(
    *,
    input_data,
    evidence_units: list[dict[str, Any]],
    final_risk_updates: list[dict[str, Any]],
    after_second_score_snapshot: dict[str, Any],
    hr_structured_draft: dict[str, Any],
    final_candidate_review_package: dict[str, Any],
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "bundle_schema_version": BUNDLE_SCHEMA_VERSION,
        "llm_schema_version": LLM_SCHEMA_VERSION,
        "policy_version": POLICY_VERSION,
        "math_contract_version": MATH_CONTRACT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "application_id": input_data.application_id,
        "input_summary": {
            "candidate_id": input_data.candidate_id,
            "job_id": input_data.job_id,
            "job_title": input_data.job_title,
            "hr_raw_note_count": len(input_data.hr_raw_notes),
            "first_risk_update_count": len(input_data.first_risk_updates) + len(input_data.internal_first_risk_updates),
        },
        "after_first_snapshot_ref": {
            "stage": input_data.after_first_score_snapshot.get("stage", "after_first_interview"),
            "score": input_data.after_first_score_snapshot.get("score"),
            "base_score": input_data.after_first_score_snapshot.get("baseScore")
            or input_data.after_first_score_snapshot.get("base_score"),
        },
        "second_interview_evidence_units": evidence_units,
        "final_risk_updates": final_risk_updates,
        "after_second_score_snapshot": after_second_score_snapshot,
        "hr_structured_draft": hr_structured_draft,
        "final_candidate_review_package": final_candidate_review_package,
        "llm_trace": {"mode": "deterministic_local", "schema_version": LLM_SCHEMA_VERSION},
        "warnings": warnings,
        "errors": [],
    }
