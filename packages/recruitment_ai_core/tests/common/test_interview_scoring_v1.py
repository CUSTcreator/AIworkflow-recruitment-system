from __future__ import annotations

from recruitment_ai_core.common.interview_scoring import (
    aggregate_round_results,
    apply_round_update,
)


def _result(
    result_id: str,
    *,
    target_type: str = "job_capability",
    target_id: str = "JDC1",
    score: float = 0.77,
    judgement: str = "verified",
    scenario_id: str | None = None,
) -> dict:
    return {
        "result_id": result_id,
        "observation_id": f"OBS_{result_id}",
        "target_type": target_type,
        "target_id": target_id,
        "level": 3,
        "score": score,
        "interviewer_judgement": judgement,
        "scenario_id": scenario_id,
    }


def test_same_scenario_is_one_proof_footprint() -> None:
    evidence, risks = aggregate_round_results(
        [
            _result("1", score=0.60, scenario_id="S1"),
            _result("2", score=0.87, scenario_id="S1"),
            _result("3", score=0.60, scenario_id="S2"),
        ],
        interview_round_id="ROUND1",
    )
    row = evidence[0]
    assert row["positive_score"] == 0.894
    assert len(row["observation_result_ids"]) == 2
    assert risks == []


def test_same_level_confirmation_only_adds_small_bonus() -> None:
    update = apply_round_update(
        0.77,
        {
            "positive_score": 0.77,
            "negative_score": None,
            "positive_judgement": "verified",
            "conflict_target_ids": [],
        },
    )
    assert update["positive_delta"] == 0.008855
    assert update["after_score"] == 0.778855


def test_negative_judgement_reduces_score_below_the_negative_anchor() -> None:
    update = apply_round_update(
        0.80,
        {
            "positive_score": None,
            "negative_score": 0.60,
            "negative_judgement": "not_support",
            "conflict_target_ids": [],
        },
    )
    assert update["negative_delta"] == 0.08
    assert update["after_score"] == 0.72


def test_positive_and_negative_same_round_are_netted() -> None:
    evidence, risks = aggregate_round_results(
        [
            _result("1", score=0.77, judgement="verified"),
            _result("2", score=0.35, judgement="contradicted"),
        ],
        interview_round_id="ROUND1",
    )
    row = evidence[0]
    assert row["conflict_target_ids"] == []
    assert apply_round_update(0.77, row)["after_score"] == 0.610855
    assert risks == []


def test_negative_delta_can_be_capped_by_the_calling_scoring_flow() -> None:
    update = apply_round_update(
        0.90,
        {
            "positive_score": None,
            "negative_score": 0.0,
            "negative_judgement": "strong_contradiction",
            "conflict_target_ids": [],
        },
        max_negative_delta=0.25,
    )

    assert update["negative_delta"] == 0.25
    assert update["after_score"] == 0.65


def test_support_and_not_support_on_same_round_target_are_netted() -> None:
    evidence, risks = aggregate_round_results(
        [
            _result("1", score=0.77, judgement="support"),
            _result("2", score=0.60, judgement="not_support"),
        ],
        interview_round_id="ROUND1",
    )
    assert evidence[0]["conflict_target_ids"] == []
    assert apply_round_update(0.80, evidence[0])["after_score"] == 0.72616
    assert risks == []


def test_preset_indicators_aggregate_to_round_framework_evidence() -> None:
    evidence, _ = aggregate_round_results(
        [
            _result("1", target_type="preset_indicator", target_id="solution_fit_scale", score=0.77),
            _result("2", target_type="preset_indicator", target_id="mechanism_engineering_depth", score=0.60),
        ],
        interview_round_id="ROUND1",
    )
    framework = next(item for item in evidence if item["target_type"] == "preset_framework")
    assert framework["target_id"] == "solution_execution"
    assert framework["positive_score"] == 0.794
    assert framework["tested_indicator_ids"] == ["mechanism_engineering_depth", "solution_fit_scale"]


def test_multiple_job_capabilities_in_one_unit_form_one_round_footprint() -> None:
    first = _result("1", target_id="JDC1", score=0.60, scenario_id="S1")
    second = _result("2", target_id="JDC2", score=0.87, scenario_id="S1")
    first["job_unit_id"] = second["job_unit_id"] = "JDU1"
    evidence, risks = aggregate_round_results(
        [first, second], interview_round_id="ROUND1"
    )
    assert risks == []
    assert evidence[0]["target_type"] == "job_unit"
    assert evidence[0]["target_id"] == "JDU1"
    assert evidence[0]["positive_score"] == 0.87
    assert evidence[0]["tested_job_capability_ids"] == ["JDC1", "JDC2"]
