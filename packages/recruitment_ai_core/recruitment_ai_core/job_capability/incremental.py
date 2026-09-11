from __future__ import annotations

import hashlib
from copy import deepcopy
from typing import Any

from recruitment_ai_core.common.interview_scoring import (
    aggregate_round_results,
    apply_round_update,
    assess_observation_targets,
)

from .current import _aggregate_job, _aggregate_job_units, assess_current_job_capability


def update_job_capability_with_interview(
    *,
    previous_result: dict[str, Any],
    resume_profile: dict[str, Any],
    interview_evidence: list[dict[str, Any]],
    new_profile_version: str,
    stage: str,
    llm_config: dict[str, Any] | None = None,
    scoring_evidence: dict[str, Any] | None = None,
    observation_target_results: list[dict[str, Any]] | None = None,
    interview_round_evidence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Recompute changed resume evidence and apply direct interview updates separately."""
    result = deepcopy(previous_result)
    job_profile = result.get("job_profile", {})
    if not job_profile.get("job_capabilities"):
        raise ValueError("current_job_requirement_profile_required")

    snapshot = scoring_evidence or {}
    resume_affected = _resume_affected_capabilities(result, snapshot)
    if resume_affected:
        result = _recalculate_resume_affected(
            result=result,
            job_profile=job_profile,
            affected_ids=resume_affected,
            scoring_evidence=snapshot,
            profile_version=new_profile_version,
            stage=stage,
            llm_config=llm_config,
        )

    target_results = observation_target_results
    round_evidence = interview_round_evidence
    observation_risks: list[dict[str, Any]] = []
    trace = None
    degraded = False
    if interview_evidence and target_results is None:
        target_results, trace, degraded = assess_observation_targets(
            interview_evidence,
            job_profile,
            llm_config,
            workflow_name=(
                "post_first_scoring"
                if stage == "after_first_interview"
                else "post_second_scoring"
            ),
        )
    target_results = list(target_results or [])
    job_target_results = [
        item for item in target_results
        if item.get("target_type") == "job_capability"
    ]
    round_id = _round_id(interview_evidence, result, stage)
    if target_results and round_evidence is None:
        round_evidence, observation_risks = aggregate_round_results(
            target_results, interview_round_id=round_id
        )
    round_evidence = list(round_evidence or [])
    job_round_evidence = [
        item for item in round_evidence
        if item.get("target_type") == "job_unit"
    ]
    capability_results = result.get("job_capability_results", [])
    tested_job_ids = _attach_job_target_results(capability_results, job_target_results)
    all_units = [
        *job_profile.get("jd_units", []),
        *job_profile.get("assessment_units", []),
    ]
    unit_results = _aggregate_job_units(
        all_units,
        job_profile.get("job_capabilities", []),
        capability_results,
    )
    affected_unit_ids = {
        str(item.get("job_unit_id"))
        for item in job_profile.get("job_capabilities", [])
        if item.get("job_capability_id") in (resume_affected | tested_job_ids) and item.get("job_unit_id")
    }
    _restore_previous_unit_updates(
        unit_results,
        previous_result.get("job_unit_results", []),
        previous_result.get("interview_round_evidence", []),
        affected_unit_ids,
    )
    # Observation 先更新 JDCapability 叶子，JDUnit 仅由同一套 V1 聚合公式向上重算。
    # 不能再把相同 Observation 直接叠加到 JDUnit，否则会重复计分。
    changed_unit_ids: set[str] = set()
    score = round(_aggregate_job(unit_results) * 100, 2)
    result.update({
        "profile_version": new_profile_version,
        "stage": stage,
        "previous_profile_version": previous_result.get("profile_version"),
        "job_capability_results": capability_results,
        "job_unit_results": unit_results,
        "observation_target_results": [
            *result.get("observation_target_results", []), *job_target_results,
        ],
        "interview_round_evidence": [
            *result.get("interview_round_evidence", []), *job_round_evidence,
        ],
        "risks": _merge_by_id(
            result.get("risks", []), observation_risks, "risk_id"
        ),
        "score_summary": {
            **result.get("score_summary", {}),
            "job_requirement_score": score,
            "job_capability_fit_score": score,
            "resume_based_score": previous_result.get("score_summary", {}).get(
                "job_requirement_score"
            ),
            "current_validated_score": score,
        },
        "change_summary": {
            "change_type": "incremental_interview_update",
            "affected_job_capability_ids": sorted(resume_affected | tested_job_ids),
            "affected_job_unit_ids": sorted(affected_unit_ids | changed_unit_ids),
            "new_evidence_ids": [
                item.get("observation_id") for item in interview_evidence
                if item.get("observation_id")
            ],
        },
        "degraded": bool(result.get("degraded") or degraded),
    })
    if trace:
        result["prompt_traces"] = [*result.get("prompt_traces", []), trace]
    return result


def _recalculate_resume_affected(
    *, result: dict[str, Any], job_profile: dict[str, Any], affected_ids: set[str],
    scoring_evidence: dict[str, Any], profile_version: str, stage: str,
    llm_config: dict[str, Any] | None,
) -> dict[str, Any]:
    subset = deepcopy(job_profile)
    subset["job_capabilities"] = [
        item for item in job_profile["job_capabilities"]
        if item["job_capability_id"] in affected_ids
    ]
    recalculated = assess_current_job_capability(
        application_id=result["application_id"],
        job_profile=subset,
        scoring_evidence=scoring_evidence,
        profile_version=profile_version,
        stage=stage,
        llm_config=llm_config,
    )
    for field in ["pair_assessments"]:
        previous_rows = result.get(field, [])
        result[field] = [
            item for item in previous_rows
            if item.get("job_capability_id") not in affected_ids
        ] + recalculated.get(field, [])
    previous_capabilities = result.get("job_capability_results", [])
    previous_by_id = {
        str(item.get("job_capability_id")): item
        for item in previous_capabilities if item.get("job_capability_id")
    }
    recalculated_capabilities = recalculated.get("job_capability_results", [])
    for item in recalculated_capabilities:
        previous_item = previous_by_id.get(str(item.get("job_capability_id")))
        if previous_item is None:
            continue
        for field in (
            "interview_target_results",
            "latest_interview_level",
            "latest_interview_judgements",
        ):
            if field in previous_item:
                item[field] = deepcopy(previous_item[field])
    merged = [
        item for item in previous_capabilities
        if item.get("job_capability_id") not in affected_ids
    ] + recalculated_capabilities
    result["job_capability_results"] = merged
    result["prompt_traces"] = [
        *result.get("prompt_traces", []), *recalculated.get("prompt_traces", [])
    ]
    return result


def _resume_affected_capabilities(
    previous: dict[str, Any], snapshot: dict[str, Any]
) -> set[str]:
    affected_projects = set(snapshot.get("affected_project_ids", []))
    corrected_claims = set(snapshot.get("corrected_skill_claim_ids", []))
    affected: set[str] = set()
    # A changed project can introduce evidence for a JD capability that had no
    # previous binding. Re-evaluate all fixed JD targets against the new snapshot.
    if affected_projects:
        affected.update(
            item.get("job_capability_id")
            for item in previous.get("job_profile", {}).get("job_capabilities", [])
        )
    for item in previous.get(
        "pair_assessments",
        [],
    ):
        if item.get("project_id") in affected_projects or (
            item.get("evidence_type") == "skill_claim"
            and item.get("evidence_id") in corrected_claims
        ):
            affected.add(item.get("job_capability_id"))
    return {item for item in affected if item}


def _apply_job_unit_round_evidence(
    unit_results: list[dict[str, Any]],
    round_evidence: list[dict[str, Any]],
) -> set[str]:
    by_unit = {str(item.get("job_unit_id")): item for item in unit_results}
    changed: set[str] = set()
    for evidence in round_evidence:
        unit_id = str(evidence["target_id"])
        unit = by_unit.get(unit_id)
        if unit is None:
            continue
        before = float(unit.get("score", 0.0))
        update = apply_round_update(before, evidence)
        after = update["after_score"]
        unit.update({
            "score": after,
            "interview_score": (
                evidence.get("positive_score")
                if evidence.get("positive_score") is not None
                else evidence.get("negative_score")
            ),
            "latest_round_evidence_id": evidence["round_evidence_id"],
        })
        evidence["score_update"] = update
        if after != before:
            changed.add(unit_id)
    return changed


def _attach_job_target_results(
    capability_results: list[dict[str, Any]],
    target_results: list[dict[str, Any]],
) -> set[str]:
    """将本轮 Observation 先作用到对应 JDCapability，再交给 JDUnit 聚合。

    这一步复用 ``apply_round_update`` 的正向确认/负向反证公式。一个岗位能力在同一
    轮的多条 Observation 会先合并为一份轮次证据；冲突证据不自动改分，只保留追溯结果。
    """
    by_capability = {
        str(item.get("job_capability_id")): item
        for item in capability_results if item.get("job_capability_id")
    }
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in target_results:
        if item.get("target_type") == "job_capability" and item.get("target_id"):
            grouped.setdefault(str(item["target_id"]), []).append(item)
    for capability_id, items in grouped.items():
        capability = by_capability.get(capability_id)
        if capability is None:
            continue
        existing = {
            str(item.get("result_id")): item
            for item in capability.get("interview_target_results", [])
            if item.get("result_id")
        }
        existing.update({str(item["result_id"]): item for item in items if item.get("result_id")})
        capability["interview_target_results"] = list(existing.values())
        positive = [item for item in items if str(item.get("interviewer_judgement") or "") in {"partial", "support", "verified"}]
        negative = [item for item in items if str(item.get("interviewer_judgement") or "") in {"not_support", "contradicted"}]
        conflict = bool(positive and negative)
        evidence = {
            "positive_score": max((float(item.get("score") or 0.0) for item in positive), default=None),
            "negative_score": min((float(item.get("score") or 0.0) for item in negative), default=None),
            "positive_judgement": str(positive[0].get("interviewer_judgement")) if positive else None,
            "negative_judgement": str(negative[0].get("interviewer_judgement")) if negative else None,
            "conflict_target_ids": [capability_id] if conflict else [],
        }
        update = apply_round_update(float(capability.get("score") or 0.0), evidence)
        capability["score"] = update["after_score"]
        capability["latest_interview_score_update"] = update
        capability["latest_interview_level"] = max(int(item.get("level") or 0) for item in items)
        capability["latest_interview_judgements"] = sorted({
            str(item.get("interviewer_judgement")) for item in items if item.get("interviewer_judgement")
        })
    return set(grouped)

def _restore_previous_unit_updates(
    current: list[dict[str, Any]],
    previous: list[dict[str, Any]],
    previous_round_evidence: list[dict[str, Any]],
    reset_unit_ids: set[str],
) -> None:
    previous_by_id = {
        str(item.get("job_unit_id")): item
        for item in previous if item.get("job_unit_id")
    }
    evidence_by_unit: dict[str, list[dict[str, Any]]] = {}
    for evidence in previous_round_evidence:
        if evidence.get("target_type") == "job_unit" and evidence.get("target_id"):
            evidence_by_unit.setdefault(str(evidence["target_id"]), []).append(evidence)
    for item in current:
        unit_id = str(item.get("job_unit_id") or "")
        old = previous_by_id.get(unit_id)
        prior_evidence = evidence_by_unit.get(unit_id, [])
        if prior_evidence:
            score = float(item.get("score", 0.0))
            for evidence in prior_evidence:
                update = apply_round_update(score, evidence)
                score = update["after_score"]
                item["interview_score"] = (
                    evidence.get("positive_score")
                    if evidence.get("positive_score") is not None
                    else evidence.get("negative_score")
                )
                item["latest_round_evidence_id"] = evidence.get("round_evidence_id")
            item["score"] = score
            continue
        if old is None or unit_id in reset_unit_ids:
            continue
        score = float(old.get("score", item.get("score", 0.0)))
        item["score"] = score
        for field in ("interview_score", "latest_round_evidence_id"):
            if field in old:
                item[field] = old[field]


def _round_id(
    observations: list[dict[str, Any]], result: dict[str, Any], stage: str
) -> str:
    for item in observations:
        if item.get("interview_round_id"):
            return str(item["interview_round_id"])
    return _id("ROUND", str(result.get("application_id") or ""), stage)


def _merge_by_id(
    first: list[dict[str, Any]], second: list[dict[str, Any]], key: str
) -> list[dict[str, Any]]:
    rows = {str(item.get(key)): item for item in first if item.get(key)}
    for item in second:
        if item.get(key):
            rows[str(item[key])] = item
    return list(rows.values())


def _id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"
