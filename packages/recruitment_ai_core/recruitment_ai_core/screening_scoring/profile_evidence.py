from __future__ import annotations

from typing import Any


def nested_work_units(resume_profile: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten the one published nested work-evidence collection for consumers."""
    output: list[dict[str, Any]] = []
    for experience in list(resume_profile.get("experience_units") or []):
        if not isinstance(experience, dict):
            continue
        project_id = str(experience.get("experience_unit_id") or experience.get("project_id") or "")
        for item in list(experience.get("work_units") or []):
            if isinstance(item, dict):
                output.append({**item, "project_id": str(item.get("project_id") or project_id)})
    return output


def nested_source_bullets(resume_profile: dict[str, Any]) -> list[dict[str, Any]]:
    """Reconstruct source evidence from nested WorkUnits without a stored duplicate."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for work_unit in nested_work_units(resume_profile):
        for ref in list(work_unit.get("source_refs") or []):
            if not isinstance(ref, dict):
                continue
            bullet_id = str(ref.get("bullet_id") or "")
            if not bullet_id or bullet_id in seen:
                continue
            seen.add(bullet_id)
            rows.append({
                "source_bullet_id": bullet_id,
                "raw_text": str(ref.get("quote") or work_unit.get("raw_text") or ""),
                "source_line_start": work_unit.get("source_line_start"),
                "source_line_end": work_unit.get("source_line_end"),
                "source_block_ids": list(work_unit.get("source_block_ids") or []),
                "work_unit_ids": [work_unit.get("work_unit_id")],
            })
    return rows


def education_records(resume_profile: dict[str, Any]) -> list[dict[str, Any]]:
    facts = dict(resume_profile.get("candidate_facts") or {})
    return [dict(item) for item in list(facts.get("education_records") or []) if isinstance(item, dict)]


