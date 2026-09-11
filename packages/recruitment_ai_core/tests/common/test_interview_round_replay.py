from recruitment_ai_core.common.interview_capability_update import (
    _restore_engineering_history,
)
from recruitment_ai_core.job_capability.incremental import (
    _restore_previous_unit_updates,
)


def _negative_evidence(target_type: str, target_id: str) -> dict:
    return {
        "round_evidence_id": "IRE_1",
        "target_type": target_type,
        "target_id": target_id,
        "positive_score": None,
        "negative_score": 0.60,
        "negative_judgement": "not_support",
        "conflict_target_ids": [],
    }


def test_job_unit_history_is_replayed_on_recalculated_resume_base() -> None:
    current = [{
        "job_unit_id": "JDU1",
        "score": 0.80,
    }]
    _restore_previous_unit_updates(
        current,
        [{"job_unit_id": "JDU1", "score": 0.70}],
        [_negative_evidence("job_unit", "JDU1")],
        {"JDU1"},
    )
    assert current[0]["score"] == 0.72


def test_preset_framework_history_is_replayed_on_recalculated_resume_base() -> None:
    profile = {
        "preset_experience_result": {
            "candidate_framework_results": [{
                "framework_id": "solution_execution", "score": 0.80,
            }],
            "interview_round_framework_evidence": [],
        },
    }
    _restore_engineering_history(
        profile,
        [],
        [_negative_evidence("preset_framework", "solution_execution")],
        [],
    )
    assert profile["preset_experience_result"]["candidate_framework_results"][0]["score"] == 0.72
    assert profile["preset_experience_result"]["interview_round_framework_evidence"][0][
        "round_evidence_id"
    ] == "IRE_1"


def test_ai03_sparse_negative_interview_changes_total_by_three_to_four_points() -> None:
    from recruitment_ai_core.common.interview_scoring import apply_round_update
    from recruitment_ai_core.job_capability.current import _aggregate_job
    from recruitment_ai_core.screening_scoring.resume_experience.aggregation import (
        aggregate_resume_score,
    )

    negative = _negative_evidence("job_unit", "JDU1")
    updated = apply_round_update(0.80, negative)["after_score"]

    job_before = _aggregate_job([
        {"score": 0.80, "aggregation_role": "required"},
        {"score": 0.75, "aggregation_role": "required"},
        {"score": 0.70, "aggregation_role": "required"},
    ])
    job_after = _aggregate_job([
        {"score": updated, "aggregation_role": "required"},
        {"score": 0.75, "aggregation_role": "required"},
        {"score": 0.70, "aggregation_role": "required"},
    ])
    engineering_before = aggregate_resume_score([
        {"framework_id": "problem_context", "score": 0.70},
        {"framework_id": "solution_execution", "score": 0.80},
        {"framework_id": "outcome_value", "score": 0.65},
    ])
    engineering_after = aggregate_resume_score([
        {"framework_id": "problem_context", "score": 0.70},
        {"framework_id": "solution_execution", "score": updated},
        {"framework_id": "outcome_value", "score": 0.65},
    ])

    total_drop = (
        0.50 * (job_before - job_after) * 100
        + 0.35 * (engineering_before - engineering_after)
    )
    assert 3.0 <= total_drop <= 4.0
