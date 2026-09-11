from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .contracts import PostSecondScoringInput


def build_hr_structured_draft(
    input_data: PostSecondScoringInput,
    evidence_units: list[dict[str, Any]],
    final_risk_updates: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "draftId": f"AI_HR_DRAFT_{input_data.application_id}",
        "applicationId": input_data.application_id,
        "interviewRound": "second",
        "sourceRawNotesId": raw_note_id,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "items": [],
    }


def build_hr_evidence_payloads(
    input_data: PostSecondScoringInput, evidence_units: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    payloads = []
    for index, unit in enumerate(
        [item for item in evidence_units if item["verification_result"] != "not_asked"],
        start=1,
    ):
        payloads.append(
            {
                "interviewEvidenceId": f"IE_HR_{input_data.application_id}_{index:03d}",
                "applicationId": input_data.application_id,
                "interviewRound": "second",
                "requirementId": _first(
                    unit.get("target_job_capability_ids", []), "SECOND_INTERVIEW"
                ),
                "riskId": _first(unit.get("risk_update_ids", []), "SECOND_INTERVIEW"),
                "sourceQuestionResponseId": unit.get("source_response_id"),
                "sourceQuestionResponseIds": (
                    [unit["source_response_id"]]
                    if unit.get("source_response_id")
                    else []
                ),
                "sourceRawNotesId": unit.get("source_note_id"),
                "sourceAssessmentId": input_data.hr_final_assessment.get("assessmentId"),
                "sourceAiDraftItemId": unit.get("source_draft_item_id"),
                "evidenceType": "interview_explanation",
                "polarity": (
                    unit["polarity"]
                    if unit["polarity"] in {"positive", "negative"}
                    else "neutral"
                ),
                "strength": _strength(unit),
                "text": unit["evidence_summary"],
            }
        )
    return payloads


def build_final_candidate_review_package(
    input_data: PostSecondScoringInput,
    score_snapshot: dict[str, Any],
    final_risk_updates: list[dict[str, Any]],
    evidence_units: list[dict[str, Any]],
) -> dict[str, Any]:
    unresolved = [
        item
        for item in final_risk_updates
        if item["status"] in {"unchanged", "increased", "new"}
    ]
    return {
        "packageId": f"FINAL_REVIEW_PKG_{input_data.application_id}",
        "applicationId": input_data.application_id,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "summary": (
            f"二面后候选人能力分 {score_snapshot.get('score')}，"
            f"仍有 {len(unresolved)} 项能力风险需要人工确认。"
        ),
        "resumeEvidenceSummary": _resume_evidence(input_data),
        "technicalFirstRoundEvidence": _first_round_evidence(input_data),
        "hrSecondRoundEvidence": [
            unit["evidence_summary"]
            for unit in evidence_units
            if unit.get("verification_result") != "not_asked"
        ][:5],
        "remainingRisks": [item["reason"] for item in unresolved],
        "humanDecisionChecklist": [item["reason"] for item in unresolved],
        "recommendedDecision": "待人工最终决策",
        "package_version": "final_candidate_review_package_v2_0",
        "stage_score_summary": {
            "after_first_score": input_data.after_first_score_snapshot.get("score"),
            "after_second_score": score_snapshot.get("score"),
            "candidate_ability_score": score_snapshot.get("candidate_ability_score"),
        },
        "resolved_risks": [
            item for item in final_risk_updates if item["status"] == "resolved"
        ],
        "unresolved_risks": unresolved,
        "new_risks": [
            item for item in final_risk_updates if item["status"] == "new"
        ],
        "role_capability_summary": score_snapshot.get("score_explanation", ""),
        "final_recommendation": "human_decision_required",
        "recommended_conditions": [item["reason"] for item in unresolved[:5]],
        "recommended_next_actions": [item["reason"] for item in unresolved[:5]],
        "rationale": score_snapshot.get("score_explanation", ""),
    }


def _resume_evidence(input_data: PostSecondScoringInput) -> list[str]:
    package = input_data.hr_second_review_package or {}
    items = package.get("confirmed_strengths") or package.get(
        "partially_confirmed_strengths"
    ) or []
    return [str(item) for item in items[:4]]


def _first_round_evidence(input_data: PostSecondScoringInput) -> list[str]:
    result = []
    for item in input_data.first_interview_evidence_units:
        text = item.get("evidence_summary") or item.get("text") or item.get("raw_text")
        if text:
            result.append(str(text))
    return result[:5]


def _strength(unit: dict[str, Any]) -> str:
    if unit["verification_result"] == "verified" and unit.get("confidence") == "high":
        return "strong"
    if unit["verification_result"] in {"verified", "partially_verified"}:
        return "moderate"
    return "weak"


def _first(items: list[str], default: str) -> str:
    return items[0] if items else default


def _first_raw_note_id(input_data: PostSecondScoringInput) -> str:
    if input_data.hr_raw_notes:
        return input_data.hr_raw_notes[-1].get(
            "rawNotesId"
        ) or f"RAW_HR_{input_data.application_id}"
    return f"RAW_HR_{input_data.application_id}"
