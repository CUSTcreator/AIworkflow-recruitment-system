from __future__ import annotations

from typing import Any


def diff_profiles(
    previous: dict[str, Any] | None,
    current: dict[str, Any],
    *,
    changed_evidence_refs: list[str] | None = None,
) -> dict[str, Any]:
    previous_results = _result_index(previous or {})
    current_results = _result_index(current)
    recomputed = [
        result_id
        for result_id, value in current_results.items()
        if result_id not in previous_results or previous_results[result_id] != value
    ]
    return {
        "changed_evidence_refs": _unique(changed_evidence_refs or []),
        "recomputed_result_refs": recomputed,
    }


def _result_index(profile: dict[str, Any]) -> dict[str, tuple[Any, ...]]:
    preset = profile.get("preset_experience_result") or {}
    job = profile.get("job_result") or {}
    rows = [
        *preset.get("project_framework_results", []),
        *preset.get("candidate_framework_results", []),
        *job.get("capability_results", []),
        *job.get("job_unit_results", []),
    ]
    result: dict[str, tuple[Any, ...]] = {}
    for item in rows:
        result_id = (
            item.get("project_framework_result_id")
            or item.get("framework_id")
            or item.get("job_capability_id")
            or item.get("job_unit_id")
        )
        if result_id:
            result[str(result_id)] = (
                item.get("score"),
                item.get("level"),
                tuple(item.get("supporting_evidence_ids", [])),
            )
    return result


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if value))
