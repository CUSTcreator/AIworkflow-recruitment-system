from __future__ import annotations

import re
from typing import Any


def build_hard_screening_facts(
    *,
    resume_profile: dict[str, Any] | None,
    candidate_facts: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build immutable hard-screening facts from the frozen ResumeProfile."""
    profile = resume_profile or {}
    profile_facts = dict(profile.get("candidate_facts") or {})
    candidate = candidate_facts or profile_facts
    highest_education = _highest_education_record(
        candidate.get("education_records")
    )
    return {
        "highest_degree": _degree_label(highest_education),
        "highest_education_status": str(
            (highest_education or {}).get("status") or ""
        ),
        "highest_education_graduation_year": _integer(
            (highest_education or {}).get("graduation_year")
        ),
        "highest_education_source_text": str(
            (highest_education or {}).get("raw_text") or ""
        ).strip(),
        "relevant_experience_years": _number(
            candidate.get("relevant_experience_years")
        ),
    }


_DEGREE_LABELS = {
    "associate": "大专",
    "bachelor": "本科",
    "undergraduate": "本科",
    "master": "硕士",
    "doctorate": "博士",
}
_DEGREE_RANKS = {
    "associate": 1,
    "bachelor": 2,
    "undergraduate": 2,
    "master": 3,
    "doctorate": 4,
}


def _highest_education_record(value: Any) -> dict[str, Any] | None:
    records = [item for item in list(value or []) if isinstance(item, dict)]
    if not records:
        return None
    return max(
        records,
        key=lambda item: _DEGREE_RANKS.get(str(item.get("degree_level") or ""), 0),
    )


def _degree_label(highest: dict[str, Any] | None) -> str:
    if not highest:
        return ""
    level = str(highest.get("degree_level") or "")
    label = _DEGREE_LABELS.get(level, "")
    if label and str(highest.get("status") or "") == "in_progress":
        return f"{label}在读"
    return label


def _number(value: Any) -> float | None:
    if isinstance(value, dict):
        value = value.get("value")
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"(\d+(?:\.\d+)?)", str(value or ""))
    return float(match.group(1)) if match else None


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    match = re.fullmatch(r"\s*(\d{4})\s*", str(value or ""))
    return int(match.group(1)) if match else None
