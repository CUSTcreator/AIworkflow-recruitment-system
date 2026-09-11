from __future__ import annotations

from copy import deepcopy
from typing import Any

from recruitment_ai_core.common.capability_profile_contracts import PROFILE_SCHEMA_VERSION, profile_id
from recruitment_ai_core.common.profile_diff import diff_profiles
from recruitment_ai_core.common.interview_state import update_interview_state
from recruitment_ai_core.common.interview_targets import build_interview_targets, merge_interview_targets
from recruitment_ai_core.common.interview_capability_update import apply_interview_capability_updates
from recruitment_ai_core.screening_scoring.score_engine import score_candidate_profile
from recruitment_ai_core.screening_scoring.strength_signals import build_strength_signals
from recruitment_ai_core.screening_scoring.weakness_signals import build_weakness_signals


def build_after_second_candidate_profile(
    *,
    input_data,
    previous_profile: dict[str, Any],
    evidence_records: list[dict[str, Any]],
    scoring_evidence: dict[str, Any],
    interview_parse_result: dict[str, Any],
    assertions: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    if not previous_profile:
        previous_profile = _fallback_profile(input_data)
    updated = deepcopy(previous_profile)
    assessment_changes: list[dict[str, Any]] = []
    risk_changes: list[dict[str, Any]] = []
    gap_changes: list[dict[str, Any]] = []
    updated["schema_version"] = PROFILE_SCHEMA_VERSION
    updated["profile_id"] = profile_id(input_data.application_id, "after_second_interview", int(previous_profile.get("version", 2)) + 1)
    updated["stage"] = "after_second_interview"
    updated["version"] = int(previous_profile.get("version", 2)) + 1
    updated["previous_profile_id"] = previous_profile.get("profile_id")
    changed_evidence_refs = _unique([item["evidence_id"] for item in evidence_records if item.get("effect") != "irrelevant"])
    llm_config = input_data.metadata.get("llm_config") if isinstance(input_data.metadata, dict) else None
    apply_interview_capability_updates(
        profile=updated,
        previous_profile=previous_profile,
        input_data=input_data,
        scoring_evidence=scoring_evidence,
        assertions=assertions,
        stage="after_second_interview",
    )
    updated.pop("risks", None)
    existing_risks: list[dict[str, Any]] = []
    risks, targets, risk_changes, target_changes, gap_changes = update_interview_state(
        profile=updated,
        parse_result=interview_parse_result,
        stage="after_second_interview",
        existing_risks=existing_risks,
        existing_targets=getattr(input_data, "interview_targets", []),
        llm_config=llm_config,
    )
    preset_result = updated.get("preset_experience_result") or {}
    job_result = updated.get("job_result") or {}
    project_indicator_results = preset_result.get(
        "project_indicator_results", []
    )
    pair_assessments = job_result.get("pair_assessments", [])
    capability_results = job_result.get("capability_results", [])
    regenerated_targets = build_interview_targets(
        application_id=input_data.application_id,
        job_profile=input_data.job_profile,
        capability_results=capability_results,
        pair_assessments=pair_assessments,
        project_indicator_results=project_indicator_results,
        stage="after_second_interview",
    )
    targets = merge_interview_targets(targets, regenerated_targets)
    updated["derived_signals"] = {
        "strengths": build_strength_signals(
            project_indicator_results=project_indicator_results,
            work_unit_indicator_results=preset_result.get(
                "work_unit_indicator_results", []
            ),
            job_capability_results=capability_results,
            pair_assessments=pair_assessments,
            job_capabilities=input_data.job_profile.get(
                "job_capabilities", []
            ),
        ),
        "weaknesses": build_weakness_signals(
            project_indicator_results=project_indicator_results,
            job_capability_results=capability_results,
            pair_assessments=pair_assessments,
            job_capabilities=input_data.job_profile.get(
                "job_capabilities", []
            ),
        ),
    }
    source_inputs = dict(updated.get("source_inputs") or {})
    parse_ids = list(source_inputs.get("interview_parse_result_ids") or [])
    parse_result_id = interview_parse_result.get("parse_result_id")
    if parse_result_id and parse_result_id not in parse_ids:
        parse_ids.append(parse_result_id)
    updated["source_inputs"] = {
        **source_inputs,
        "resume_profile_version_id": updated.get("resume_profile_version_id"),
        "job_profile_version_id": updated.get("job_profile_version_id"),
        "interview_parse_result_ids": parse_ids,
    }
    score_engine_input, score_engine_output = score_candidate_profile(updated)
    updated["scores"] = {
        "job_requirement": score_engine_output["job_capability_fit_score"] / 100,
        "preset_experience": score_engine_output["resume_experience_score"] / 100,
        "education": score_engine_output["education_background_score"] / 100,
        "total": score_engine_output["base_score"] / 100,
    }
    updated["interview_target_ids"] = [
        item["interview_target_id"]
        for item in targets if item.get("interview_target_id")
    ]
    updated.pop("risks", None)
    updated.pop("interview_targets", None)
    updated["change_summary"] = diff_profiles(previous_profile, updated, changed_evidence_refs=changed_evidence_refs)
    updated.setdefault("metadata", {})["second_interview_policy"] = (
        "non_scoring_information_is_display_only;ability_evidence_is_incremental"
    )
    return updated, assessment_changes, risk_changes, gap_changes, target_changes, score_engine_input, score_engine_output, risks, targets


def _fallback_profile(input_data) -> dict[str, Any]:
    return {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "profile_id": profile_id(input_data.application_id, "after_first_interview", 2),
        "application_id": input_data.application_id,
        "candidate_id": input_data.candidate_id,
        "job_id": input_data.job_id,
        "stage": "after_first_interview",
        "version": 2,
        "job_profile_version_id": input_data.job_profile.get("job_profile_version_id", "unknown_job_profile"),
        "resume_profile_version_id": input_data.resume_profile.get("resume_profile_version_id", "unknown_resume_profile"),
        "previous_profile_id": None,
        "preset_model_id": input_data.job_profile.get("preset_model_id"),
        "preset_model_version": input_data.job_profile.get("preset_model_version"),
        "algorithm_versions": {},
        "source_inputs": {"interview_parse_result_ids": []},
        "weights": {"job_requirement": 0.5, "preset_experience": 0.35, "education": 0.15},
        "scores": {"job_requirement": 0.0, "preset_experience": 0.0, "education": 0.0, "total": 0.0},
        "preset_experience_result": {"project_framework_results": [], "observation_target_results": [], "interview_round_framework_evidence": [], "candidate_framework_results": [], "candidate_framework_updates": []},
        "job_result": {"capability_results": [], "job_unit_results": [], "observation_target_results": [], "interview_round_evidence": []},
        "education_result": {"score": 0.0, "undergraduate": None, "master": None},
        "derived_signals": {"strengths": [], "weaknesses": []},
        "risk_ids": [],
        "interview_target_ids": [],
        "change_summary": {"changed_evidence_refs": [], "recomputed_result_refs": []},
        "metadata": {"fallback": True},
    }


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _merge_objects(
    first: list[dict[str, Any]],
    second: list[dict[str, Any]],
    key: str,
) -> list[dict[str, Any]]:
    output = {
        str(item[key]): deepcopy(item)
        for item in first if item.get(key)
    }
    output.update({
        str(item[key]): deepcopy(item)
        for item in second if item.get(key)
    })
    return list(output.values())
