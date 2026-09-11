from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def _first_value(source: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = source.get(key)
        if value is not None:
            return value
    return None


def build_candidate_decision_overview(
    *,
    screening_result: dict[str, Any],
    decision_summary: dict[str, Any] | None,
    decision_support: dict[str, Any] | None = None,
    score_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Map the persisted stage summary to the page-facing read model."""
    summary = score_snapshot or screening_result.get("summary") or {}
    artifact = decision_summary or {}
    if artifact.get("summary_version"):
        return _current_overview(summary, artifact)
    return _legacy_overview(summary, artifact, decision_support or {})


def _public_recommendation_level(value: Any) -> str:
    """推荐程度来自后端策略；无效值安全降级为需核验的通过建议。"""
    level = str(value or "")
    return level if level in {
        "strongly_recommend", "recommend", "cautious_recommend", "not_recommend", "strongly_not_recommend"
    } else "cautious_recommend"

def _current_overview(
    score_snapshot: dict[str, Any],
    artifact: dict[str, Any],
) -> dict[str, Any]:
    recommendation = artifact.get("recommendation") or {}
    ai = artifact.get("ai_summary") or {}
    focus = artifact.get("verification_focus") or {}
    return {
        "assessmentStage": str(artifact.get("stage") or "screening"),
        "assessment": _assessment(score_snapshot),
        "aiSummary": {
            "recommendationLevel": _public_recommendation_level(recommendation.get("level")),
            "recommendationReason": ai.get(
                "recommendation_reason",
                recommendation.get("reason", ""),
            ),
            "strengths": [
                _summary_item(item)
                for item in ai.get("strengths", [])
                if isinstance(item, dict)
            ],
            "weaknesses": [
                _summary_item(item)
                for item in ai.get("weaknesses", [])
                if isinstance(item, dict)
            ],
            "generationMode": ai.get(
                "generation_mode", "rule_fallback"
            ),
        },
        "verificationFocus": {
            "items": [
                _focus_item(item)
                for item in focus.get("items", [])
                if isinstance(item, dict)
            ],
            "generationMode": focus.get(
                "generation_mode", "rule_fallback"
            ),
        },
        "sourceSnapshotHash": str(
            artifact.get("source_snapshot_hash") or ""
        ),
        "generatedAt": str(
            artifact.get("generated_at")
            or datetime.now(UTC).isoformat()
        ),
    }


def _assessment(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "totalScore": _first_value(
            source,
            "overallScore",
            "baseScore",
            "base_score",
            "candidate_ability_score",
            "score",
        ),
        "jobFitScore": _first_value(
            source,
            "jobCapabilityFitScore",
            "job_capability_fit_score",
        ),
        "experienceScore": _first_value(
            source,
            "resumeExperienceScore",
            "resume_experience_score",
        ),
        "educationScore": _first_value(
            source,
            "educationBackgroundScore",
            "education_background_score",
        ),
        "qualificationStatus": _first_value(
            source,
            "qualificationStatus",
            "qualification_status",
        ) or "unclear",
    }


def _summary_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": str(item.get("title") or ""),
        "summary": str(item.get("summary") or ""),
        "sourceSignalIds": [
            str(value) for value in item.get("source_signal_ids", []) if value
        ],
        "evidenceIds": [
            str(value) for value in item.get("evidence_ids", [])
        ],
    }


def _focus_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "focusId": str(item.get("focus_id") or ""),
        "focusType": str(
            item.get("focus_type") or "experience_verification"
        ),
        "title": str(item.get("title") or ""),
        "reason": str(item.get("reason") or ""),
        "verificationGoal": str(
            item.get("verification_goal") or ""
        ),
        "sourceInterviewTargetIds": [
            str(value)
            for value in item.get("source_interview_target_ids", [])
            if value
        ],
        "status": str(item.get("status") or "open"),
        "evidenceIds": [
            str(value) for value in item.get("evidence_ids", [])
        ],
    }


def _legacy_overview(
    summary: dict[str, Any],
    artifact: dict[str, Any],
    decision_support: dict[str, Any],
) -> dict[str, Any]:
    recommendation = artifact.get("recommendation") or {}
    strengths = [
        _legacy_summary_item(item)
        for item in artifact.get("strengths", [])
        if isinstance(item, dict)
    ]
    weaknesses = [
        _legacy_summary_item(item)
        for item in artifact.get("risks", [])
        if isinstance(item, dict)
    ]
    focus_items = []
    for item in (
        (decision_support.get("decisionFocus") or {}).get("items")
        or []
    ):
        focus_items.append({
            "focusId": str(item.get("focusId") or ""),
            "focusType": "experience_verification",
            "title": str(item.get("title") or ""),
            "reason": str(item.get("oneLineReason") or ""),
            "verificationGoal": str(
                item.get("verificationAction") or ""
            ),
            "sourceInterviewTargetIds": [],
            "status": str(item.get("status") or "open"),
            "evidenceIds": [
                str(value)
                for value in item.get("sourceRefs") or []
                if str(value).strip()
            ],
        })
    mode = artifact.get("generationMode", "rule_fallback")
    return {
        "assessmentStage": str(artifact.get("stage") or "screening"),
        "assessment": _assessment(summary),
        "aiSummary": {
            "recommendationLevel": _public_recommendation_level(recommendation.get("level")),
            "recommendationReason": recommendation.get("reason", ""),
            "strengths": strengths,
            "weaknesses": weaknesses,
            "generationMode": mode,
        },
        "verificationFocus": {
            "items": focus_items,
            "generationMode": mode,
        },
        "sourceSnapshotHash": str(
            artifact.get("sourceSnapshotHash") or ""
        ),
        "generatedAt": str(
            artifact.get("generatedAt")
            or datetime.now(UTC).isoformat()
        ),
    }


def _legacy_summary_item(item: dict[str, Any]) -> dict[str, Any]:
    text = str(item.get("text") or "")
    evidence_ids = [
        str(ref.get("id"))
        for ref in item.get("sourceRefs", [])
        if isinstance(ref, dict)
        and ref.get("type") in {"evidence", "interview_evidence"}
        and ref.get("id")
    ]
    return {
        "title": text,
        "summary": text,
        "sourceSignalIds": [],
        "evidenceIds": evidence_ids,
    }
