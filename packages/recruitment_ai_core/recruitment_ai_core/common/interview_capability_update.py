from __future__ import annotations

from typing import Any

from recruitment_ai_core.job_capability import update_job_capability_with_interview
from recruitment_ai_core.common.interview_scoring import (
    aggregate_round_results,
    apply_round_update,
    assess_observation_targets,
)
from recruitment_ai_core.screening_scoring.resume_experience.aggregation import (
    aggregate_resume_score,
)
from recruitment_ai_core.screening_scoring.resume_experience.pipeline import (
    reassess_resume_experience,
)


def apply_interview_capability_updates(
    *,
    profile: dict[str, Any],
    previous_profile: dict[str, Any],
    input_data: Any,
    scoring_evidence: dict[str, Any],
    assertions: list[dict[str, Any]],
    stage: str,
) -> None:
    """Incrementally recompute only ability assessments affected by interview evidence."""
    llm_config = input_data.metadata.get("llm_config") if isinstance(input_data.metadata, dict) else None
    previous_preset_experience_result = previous_profile.get("preset_experience_result", {})
    previous_framework_evidence = list(
        previous_preset_experience_result.get("interview_round_framework_evidence", [])
    )
    previous_engineering_targets = list(
        previous_preset_experience_result.get("observation_target_results", [])
    )
    previous_framework_updates = list(
        previous_preset_experience_result.get("candidate_framework_updates", [])
    )
    resume_result = reassess_resume_experience(scoring_evidence, llm_config)
    if resume_result is not None:
        apply_resume_experience_result(profile, resume_result)
        _restore_engineering_history(
            profile,
            previous_engineering_targets,
            previous_framework_evidence,
            previous_framework_updates,
        )

    target_results: list[dict[str, Any]] = []
    round_evidence: list[dict[str, Any]] = []
    observation_risks: list[dict[str, Any]] = []
    trace = None
    if assertions and input_data.job_profile:
        target_results, trace, degraded = assess_observation_targets(
            assertions,
            input_data.job_profile,
            llm_config if isinstance(llm_config, dict) else None,
            workflow_name=(
                "post_first_scoring"
                if stage == "after_first_interview"
                else "post_second_scoring"
            ),
        )
        if target_results:
            round_id = next(
                (
                    item.get("interview_round_id")
                    for item in assertions
                    if item.get("interview_round_id")
                ),
                f"ROUND_{profile.get('application_id')}_{stage}",
            )
            round_evidence, observation_risks = aggregate_round_results(
                target_results, interview_round_id=str(round_id)
            )
        profile.setdefault("metadata", {})["interview_observation_scoring"] = {
            "degraded": degraded,
            "trace": trace,
        }
    _apply_engineering_observations(profile, target_results, round_evidence)

    if (
        (previous_profile.get("job_result") or {}).get("capability_results")
        and input_data.job_profile
        and input_data.resume_profile
    ):
        result = update_job_capability_with_interview(
            previous_result=job_capability_result(previous_profile, input_data.job_profile),
            resume_profile=input_data.resume_profile,
            interview_evidence=assertions,
            new_profile_version=profile["profile_id"],
            stage=stage,
            scoring_evidence=scoring_evidence,
            llm_config=llm_config,
            observation_target_results=target_results,
            interview_round_evidence=round_evidence,
        )
        apply_job_capability_result(profile, result)
    if observation_risks:
        current = {
            item.get("risk_id"): item for item in profile.get("risks", [])
            if item.get("risk_id")
        }
        current.update({item["risk_id"]: item for item in observation_risks})
        profile["risks"] = list(current.values())


def job_capability_result(
    profile: dict[str, Any], job_profile: dict[str, Any]
) -> dict[str, Any]:
    job_result = profile.get("job_result") or {}
    return {
        "application_id": profile.get("application_id"),
        "profile_version": profile.get("profile_id"),
        "stage": profile.get("stage"),
        "job_profile": job_profile,
        "pair_assessments": job_result.get("pair_assessments", []),
        "job_capability_results": job_result.get("capability_results", []),
        "job_unit_results": job_result.get("job_unit_results", []),
        "risks": [],
        "observation_target_results": job_result.get("observation_target_results", []),
        "interview_round_evidence": job_result.get("interview_round_evidence", []),
        "prompt_traces": profile.get("metadata", {}).get("job_prompt_traces", []),
    }


def apply_job_capability_result(
    profile: dict[str, Any], result: dict[str, Any]
) -> None:
    previous = profile.get("job_result") or {}
    profile["job_result"] = {
        "pair_assessments": result.get(
            "pair_assessments", previous.get("pair_assessments", [])
        ),
        "capability_results": result.get("job_capability_results", previous.get("capability_results", [])),
        "job_unit_results": result.get("job_unit_results", previous.get("job_unit_results", [])),
        "observation_target_results": result.get("observation_target_results", previous.get("observation_target_results", [])),
        "interview_round_evidence": result.get("interview_round_evidence", previous.get("interview_round_evidence", [])),
    }
    if "risks" in result:
        profile["risks"] = result["risks"]
    if result.get("prompt_traces"):
        profile.setdefault("metadata", {})["job_prompt_traces"] = result["prompt_traces"]
    profile.setdefault("metadata", {})["job_capability_change_summary"] = result.get(
        "change_summary", {}
    )


def apply_resume_experience_result(
    profile: dict[str, Any], result: dict[str, Any]
) -> None:
    previous_engineering = profile.get("preset_experience_result", {})
    profile.setdefault("metadata", {})["resume_experience_change_summary"] = result.get(
        "change_summary", {}
    )
    profile["preset_experience_result"] = {
        **previous_engineering,
        "work_unit_indicator_results": result.get(
            "work_unit_indicator_results",
            previous_engineering.get("work_unit_indicator_results", []),
        ),
        "project_pao_results": result.get(
            "project_pao_results",
            previous_engineering.get("project_pao_results", []),
        ),
        "project_indicator_results": result.get(
            "project_indicator_results",
            previous_engineering.get("project_indicator_results", []),
        ),
        "project_framework_results": result.get("project_framework_results", []),
        "candidate_framework_results": result.get("candidate_framework_results", []),
    }


def _apply_engineering_observations(
    profile: dict[str, Any],
    target_results: list[dict[str, Any]],
    round_evidence: list[dict[str, Any]],
) -> None:
    preset_experience_results = [
        item for item in target_results
        if item.get("target_type") == "preset_indicator"
    ]
    framework_evidence = [
        item for item in round_evidence
        if item.get("target_type") == "preset_framework"
    ]
    if not preset_experience_results and not framework_evidence:
        return
    preset_result = profile.setdefault("preset_experience_result", {})
    frameworks = preset_result.get("candidate_framework_results", [])
    by_framework = {item.get("framework_id"): item for item in frameworks}
    updates: list[dict[str, Any]] = []
    for evidence in framework_evidence:
        framework = by_framework.get(evidence.get("target_id"))
        if framework is None:
            continue
        before = float(framework.get("score", 0.0))
        update = apply_round_update(before, evidence)
        framework["score"] = update["after_score"]
        evidence["score_update"] = update
        updates.append({
            "framework_id": framework["framework_id"],
            "round_evidence_id": evidence["round_evidence_id"],
            **update,
        })
    score = aggregate_resume_score(frameworks)
    preset_result["candidate_framework_results"] = frameworks
    preset_result["observation_target_results"] = [
        *preset_result.get("observation_target_results", []), *preset_experience_results,
    ]
    preset_result["interview_round_framework_evidence"] = [
        *preset_result.get("interview_round_framework_evidence", []), *framework_evidence,
    ]
    preset_result["candidate_framework_updates"] = [
        *preset_result.get("candidate_framework_updates", []), *updates,
    ]


def _restore_engineering_history(
    profile: dict[str, Any],
    target_results: list[dict[str, Any]],
    framework_evidence: list[dict[str, Any]],
    framework_updates: list[dict[str, Any]],
) -> None:
    if not (target_results or framework_evidence or framework_updates):
        return
    preset_result = profile.setdefault("preset_experience_result", {})
    frameworks = preset_result.get("candidate_framework_results", [])
    by_framework = {str(item.get("framework_id")): item for item in frameworks}
    for evidence in framework_evidence:
        framework = by_framework.get(str(evidence.get("target_id")))
        if framework is None:
            continue
        update = apply_round_update(float(framework.get("score", 0.0)), evidence)
        framework["score"] = update["after_score"]
    score = aggregate_resume_score(frameworks)
    profile["preset_experience_result"] = {
        **preset_result,
        "observation_target_results": list(target_results),
        "interview_round_framework_evidence": list(framework_evidence),
        "candidate_framework_results": frameworks,
        "candidate_framework_updates": list(framework_updates),
    }
