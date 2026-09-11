from __future__ import annotations

import pytest

from recruitment_ai_core.screening_scoring.resume_experience.aggregation import (
    aggregate_candidate_frameworks,
    aggregate_project_frameworks,
)
from recruitment_ai_core.screening_scoring.resume_experience.pipeline import (
    _filter_low_score_projects,
)


def test_project_indicators_aggregate_to_project_framework_before_cross_project() -> None:
    project_frameworks = aggregate_project_frameworks(
        "P_1",
        [
            {"indicator_id": "solution_fit_scale", "score": 1.0},
            {"indicator_id": "key_difficulty_resolution", "score": 0.35},
        ],
    )
    solution_framework = next(item for item in project_frameworks if item["framework_id"] == "solution_execution")
    assert solution_framework["score"] == pytest.approx(0.8305)
    assert solution_framework["active_indicator_ids"] == [
        "solution_fit_scale", "key_difficulty_resolution"
    ]


def test_candidate_framework_uses_independent_project_framework_support() -> None:
    frameworks = aggregate_candidate_frameworks(
        [
            {"project_framework_result_id": "PDR_1", "project_id": "P_1", "framework_id": "solution_execution", "score": 0.8},
            {"project_framework_result_id": "PDR_2", "project_id": "P_2", "framework_id": "solution_execution", "score": 0.35},
        ]
    )
    solution = next(item for item in frameworks if item["framework_id"] == "solution_execution")
    assert solution["score"] == pytest.approx(0.821)
    assert solution["primary_project_framework_result_id"] == "PDR_1"
    assert solution["supplemental_project_framework_result_ids"] == ["PDR_2"]


def _project_entry(project_id: str, scores: tuple[float, float, float]) -> dict:
    return {
        "project": {
            "project_id": project_id,
            "project_framework_results": [
                {"framework_id": framework_id, "score": score}
                for framework_id, score in zip(
                    ("problem_context", "solution_execution", "outcome_value"),
                    scores,
                )
            ],
        },
        "indicators": [],
        "work_units": [],
    }


def test_low_score_tail_is_removed_after_stable_high_group() -> None:
    entries = [
        _project_entry("P_HIGH_1", (0.80, 0.80, 0.80)),
        _project_entry("P_LOW_1", (0.35, 0.35, 0.35)),
        _project_entry("P_HIGH_2", (0.75, 0.75, 0.75)),
        _project_entry("P_LOW_2", (0.35, 0.35, 0.35)),
    ]

    kept = _filter_low_score_projects(entries)

    assert [item["project"]["project_id"] for item in kept] == [
        "P_HIGH_1", "P_HIGH_2"
    ]


def test_low_score_filter_requires_two_projects_above_gap() -> None:
    entries = [
        _project_entry("P_HIGH", (0.80, 0.80, 0.80)),
        _project_entry("P_LOW_1", (0.35, 0.35, 0.35)),
        _project_entry("P_LOW_2", (0.35, 0.35, 0.35)),
    ]

    kept = _filter_low_score_projects(entries)

    assert len(kept) == 3


def test_low_score_filter_is_not_applied_to_fewer_than_three_projects() -> None:
    entries = [
        _project_entry("P_LOW", (0.0, 0.0, 0.0)),
        _project_entry("P_GOOD", (0.8, 0.8, 0.8)),
    ]

    kept = _filter_low_score_projects(entries)

    assert len(kept) == 2


def test_low_score_filter_keeps_projects_with_small_gap() -> None:
    entries = [
        _project_entry("P_1", (0.50, 0.50, 0.50)),
        _project_entry("P_2", (0.60, 0.60, 0.60)),
        _project_entry("P_3", (0.70, 0.70, 0.70)),
    ]

    kept = _filter_low_score_projects(entries)

    assert len(kept) == 3


def test_low_score_filter_keeps_degraded_project_outside_distribution() -> None:
    degraded = _project_entry("P_DEGRADED", (0.05, 0.05, 0.05))
    degraded["project"]["degraded"] = True
    entries = [
        _project_entry("P_HIGH_1", (0.80, 0.80, 0.80)),
        _project_entry("P_HIGH_2", (0.75, 0.75, 0.75)),
        _project_entry("P_LOW_1", (0.35, 0.35, 0.35)),
        _project_entry("P_LOW_2", (0.35, 0.35, 0.35)),
        degraded,
    ]

    kept = _filter_low_score_projects(entries)

    assert [item["project"]["project_id"] for item in kept] == [
        "P_HIGH_1", "P_HIGH_2", "P_DEGRADED"
    ]


def test_low_score_filter_uses_later_split_when_maximum_gaps_are_equal() -> None:
    entries = [
        _project_entry("P_90", (0.90, 0.90, 0.90)),
        _project_entry("P_65", (0.65, 0.65, 0.65)),
        _project_entry("P_40", (0.40, 0.40, 0.40)),
        _project_entry("P_15", (0.15, 0.15, 0.15)),
    ]

    kept = _filter_low_score_projects(entries)

    assert [item["project"]["project_id"] for item in kept] == [
        "P_90", "P_65", "P_40"
    ]
