from __future__ import annotations

from typing import Any

from .contracts import PostSecondScoringInput


def build_final_risk_updates(
    input_data: PostSecondScoringInput,
    evidence_units: list[dict[str, Any]],
    canonical_risks: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Expose ability-model risks only; non-scoring HR information is not a risk."""
    risks = canonical_risks or []
    output = []
    for index, risk in enumerate(risks, start=1):
        risk_id = risk.get("risk_id") or risk.get("riskId") or f"RISK_{index:03d}"
        status = risk.get("status", "open")
        output.append(
            {
                "risk_update_id": f"RU_FINAL_{input_data.application_id}_{index:03d}",
                "source_risk_update_id": None,
                "risk_id": risk_id,
                "risk_type": risk.get("category", "ability_risk"),
                "source": "candidate_capability_profile",
                "status": (
                    "resolved"
                    if status == "resolved"
                    else (
                        "new"
                        if risk.get("stage_created") == "after_second_interview"
                        else "unchanged"
                    )
                ),
                "severity_after_second": risk.get("severity", "medium"),
                "evidence_unit_ids": [],
                "reason": (
                    risk.get("resolution_note")
                    or risk.get("reason")
                    or risk.get("title")
                    or risk_id
                ),
            }
        )
    return output
