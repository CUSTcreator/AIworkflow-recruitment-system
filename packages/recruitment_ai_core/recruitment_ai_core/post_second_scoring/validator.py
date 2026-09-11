from __future__ import annotations

from typing import Any


def validate_result(result: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    if result.get("status") != "scored":
        warnings.append("status_not_scored")
    score = result.get("after_second_score_snapshot", {})
    if score.get("stage") != "after_second_interview":
        warnings.append("missing_after_second_stage")
    if "score" not in score:
        warnings.append("missing_score_snapshot_score")
    package = result.get("final_candidate_review_package", {})
    for key in [
        "packageId",
        "applicationId",
        "summary",
        "resumeEvidenceSummary",
        "technicalFirstRoundEvidence",
        "hrSecondRoundEvidence",
        "remainingRisks",
        "humanDecisionChecklist",
        "recommendedDecision",
    ]:
        if key not in package:
            warnings.append(f"missing_final_package_field:{key}")
    return warnings
