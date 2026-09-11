"""Deterministic recommendation policy for assessment presentation.

The score engine produces measurements; this module maps those measurements to a
human-facing recommendation.  The mapping is deliberately versioned and
independent from the LLM so it can be calibrated against recruiter labels later.
"""
from __future__ import annotations

from typing import Any


POLICY_VERSION = "recommendation_policy_v1"
RECOMMENDATION_LEVELS = (
    "strongly_recommend",
    "recommend",
    "cautious_recommend",
    "not_recommend",
    "strongly_not_recommend",
)

# These are launch thresholds for the current score scale, not a claim about
# recruiter truth.  Keep them in one place so calibration creates a new policy
# version instead of changing historical assessment semantics silently.
POLICY_THRESHOLDS = {
    "strongly_recommend": {"total": 80.0, "job_fit": 72.0, "experience": 85.0},
    "recommend": {"total": 68.0, "job_fit": 55.0, "experience": 65.0},
    "cautious_recommend": {"total": 55.0, "job_fit": 45.0, "experience": 55.0},
    "not_recommend": {"total": 55.0, "job_fit": 40.0, "experience": 65.0},
    "strongly_not_recommend": {"total": 40.0, "job_fit": 25.0},
}

_STAGE_LABELS = {
    "screening": {
        "strongly_recommend": "强烈建议通过初筛",
        "recommend": "建议通过初筛",
        "cautious_recommend": "建议通过初筛，需重点核验",
        "not_recommend": "建议不通过初筛",
        "strongly_not_recommend": "明确不通过初筛",
    },
    "after_first_interview": {
        "strongly_recommend": "强烈建议进入二面",
        "recommend": "建议进入二面",
        "cautious_recommend": "建议进入二面，需重点核验",
        "not_recommend": "建议不进入二面",
        "strongly_not_recommend": "明确不进入二面",
    },
    "after_second_interview": {
        "strongly_recommend": "强烈建议录用",
        "recommend": "建议录用",
        "cautious_recommend": "建议录用，需重点核验",
        "not_recommend": "建议不录用",
        "strongly_not_recommend": "明确不录用",
    },
}


def compute_recommendation_level(
    score_result: dict[str, Any] | None,
    *,
    hard_screening_status: str | None = None,
    has_critical_gap: bool = False,
) -> str:
    """Return the stable five-level recommendation code.

    Hard-screen failure is an explicit upper-layer gate.  A critical capability
    gap caps a positive score at the cautious level; ordinary weaknesses remain
    explanatory signals and are not deducted a second time from the score.
    """
    scores = _scores(score_result)
    if str(hard_screening_status or "") == "failed":
        return "strongly_not_recommend"

    total, job_fit, experience = scores["total"], scores["job_fit"], scores["experience"]
    if total is None or job_fit is None or experience is None:
        return "cautious_recommend"

    if total < 40 or (job_fit < 25 and total < 55):
        level = "strongly_not_recommend"
    elif total < 55 or (job_fit < 40 and experience < 65):
        level = "not_recommend"
    elif total >= 80 and job_fit >= 72 and experience >= 85:
        level = "strongly_recommend"
    elif total >= 68 and job_fit >= 55 and experience >= 65:
        level = "recommend"
    else:
        level = "cautious_recommend"

    if has_critical_gap and level in {"strongly_recommend", "recommend"}:
        return "cautious_recommend"
    return level


def recommendation_display_text(level: str, stage: str) -> str:
    """Map an internal level to the user language for the current stage."""
    normalized = level if level in RECOMMENDATION_LEVELS else "cautious_recommend"
    return _STAGE_LABELS.get(stage, _STAGE_LABELS["screening"])[normalized]


def _scores(value: dict[str, Any] | None) -> dict[str, float | None]:
    source = dict(value or {})

    def number(*names: str) -> float | None:
        for name in names:
            item = source.get(name)
            if isinstance(item, (int, float)) and not isinstance(item, bool):
                return float(item)
        return None

    return {
        "total": number("total", "base_score", "baseScore"),
        "job_fit": number("job_fit", "job_capability_fit_score", "jobCapabilityFitScore"),
        "experience": number("experience", "resume_experience_score", "resumeExperienceScore"),
    }

