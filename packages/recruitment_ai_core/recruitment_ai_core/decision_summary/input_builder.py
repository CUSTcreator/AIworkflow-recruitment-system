from __future__ import annotations

from typing import Any


def evidence_ids_for_result_ids(
    result_ids: list[str],
    candidate_profile: dict[str, Any],
) -> list[str]:
    """Resolve persisted result IDs to evidence IDs without rebuilding display inputs."""
    index = _result_index(candidate_profile)
    result: list[str] = []
    for result_id in result_ids:
        for evidence_id in index.get(str(result_id), {}).get("evidence_ids", []):
            if evidence_id and evidence_id not in result:
                result.append(evidence_id)
    return result


def _result_index(candidate_profile: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Build the narrow result-to-evidence index needed by rule derivation."""
    output: dict[str, dict[str, Any]] = {}
    preset = candidate_profile.get("preset_experience_result") or {}
    work_units = {
        str(item.get("work_unit_indicator_result_id")): item
        for item in preset.get("work_unit_indicator_results", [])
        if item.get("work_unit_indicator_result_id")
    }
    for result_id, item in work_units.items():
        output[result_id] = {"evidence_ids": _strings([item.get("work_unit_id")])}
    for item in preset.get("project_indicator_results", []):
        result_id = str(item.get("project_indicator_result_id") or "")
        if not result_id:
            continue
        evaluation = item.get("project_evaluation") or {}
        output[result_id] = {
            "evidence_ids": _strings(evaluation.get("work_unit_ids", [])),
        }
    job = candidate_profile.get("job_result") or {}
    pairs = {
        str(item.get("pair_id")): item
        for item in job.get("pair_assessments", [])
        if item.get("pair_id")
    }
    for item in job.get("capability_results", []):
        result_id = str(item.get("job_capability_result_id") or "")
        if not result_id:
            continue
        pair_ids = _strings([
            item.get("primary_pair_id"),
            *list(item.get("supplemental_pair_ids", [])),
        ])
        evidence_ids: list[str] = []
        for pair_id in pair_ids:
            pair = pairs.get(pair_id) or {}
            for evidence_id in _strings([
                *list(pair.get("proof_work_unit_ids", [])),
                pair.get("evidence_id"),
            ]):
                if evidence_id not in evidence_ids:
                    evidence_ids.append(evidence_id)
        output[result_id] = {"evidence_ids": evidence_ids}
    return output


def _strings(values: Any) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if value)) if isinstance(values, list) else []
