from __future__ import annotations

from collections import defaultdict
from typing import Any

from .contracts import LEVEL_STRENGTH
from .preset_models import get_preset_model

LOCAL_SUPPORT_WEIGHTS = (0.03, 0.02)
LOCAL_GLOBAL_WEIGHTS = (0.40, 0.60)
CROSS_PROJECT_WEIGHTS = (0.06, 0.04)
FRAMEWORK_WEIGHTS = (0.80, 0.08, 0.12)
RESUME_FRAMEWORK_WEIGHTS = {
    "problem_context": 0.20,
    "solution_execution": 0.50,
    "outcome_value": 0.30,
}
RESUME_BLEND_WEIGHTS = (0.45, 0.55)
REVIEW_LEVEL_GAP = 2


def _model(preset_model: dict[str, Any] | None) -> dict[str, Any]:
    return preset_model or get_preset_model()


def aggregate_project_indicators(
    project_id: str,
    unit_results: list[dict[str, Any]],
    project_results: list[dict[str, Any]],
    preset_model: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    model = _model(preset_model)
    by_unit: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in unit_results:
        by_unit[item["indicator_id"]].append(item)
    by_project = {item["indicator_id"]: item for item in project_results}
    output = []
    for spec in model["indicators"]:
        indicator_id = spec["indicator_id"]
        selected = _select_independent(by_unit.get(indicator_id, []), "score")
        local = _support_score(
            [float(item["score"]) for item in selected],
            LOCAL_SUPPORT_WEIGHTS,
        )
        global_result = by_project.get(indicator_id)
        global_score = (
            LEVEL_STRENGTH[global_result["level"]] if global_result else 0.0
        )
        if local == 0 and global_score == 0:
            continue
        score = (
            LOCAL_GLOBAL_WEIGHTS[0] * local
            + LOCAL_GLOBAL_WEIGHTS[1] * global_score
        )
        local_level = selected[0]["level"] if selected else 0
        global_level = global_result["level"] if global_result else 0
        output.append({
            "project_indicator_result_id": f"PIR_{project_id}_{indicator_id}",
            "project_id": project_id,
            "indicator_id": indicator_id,
            "level": global_level,
            "local_result": ({
                "score": round(local, 6),
                "primary_work_unit_result_id": selected[0][
                    "work_unit_indicator_result_id"
                ],
                "supplemental_work_unit_result_ids": [
                    item["work_unit_indicator_result_id"] for item in selected[1:]
                ],
            } if selected else None),
            "project_evaluation": ({
                "level": global_level,
                "score": round(global_score, 6),
                "work_unit_ids": list(global_result.get("work_unit_ids", [])),
                "reason": str(global_result.get("reason") or ""),
            } if global_result else None),
            "score": round(min(1.0, score), 6),
            "review_required": abs(local_level - global_level) >= REVIEW_LEVEL_GAP,
        })
    return output


def aggregate_project_frameworks(
    project_id: str,
    project_indicator_results: list[dict[str, Any]],
    preset_model: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    model = _model(preset_model)
    by_indicator = {
        item["indicator_id"]: item for item in project_indicator_results
    }
    output = []
    for framework in model["frameworks"]:
        framework_id = framework["framework_id"]
        ids = list(framework["indicator_ids"])
        scores = [
            float(by_indicator.get(indicator_id, {}).get("score", 0))
            for indicator_id in ids
        ]
        score = hierarchical_score(scores)
        active = [
            by_indicator[indicator_id]
            for indicator_id in ids
            if indicator_id in by_indicator
        ]
        output.append({
            "project_framework_result_id": f"PDR_{project_id}_{framework_id}",
            "project_id": project_id,
            "framework_id": framework_id,
            "score": round(score, 6),
            "active_indicator_ids": [
                item["indicator_id"] for item in active
            ],
        })
    return output


def aggregate_candidate_frameworks(
    project_framework_results: list[dict[str, Any]],
    preset_model: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    model = _model(preset_model)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in project_framework_results:
        grouped[item["framework_id"]].append(item)
    output = []
    for framework in model["frameworks"]:
        framework_id = framework["framework_id"]
        ordered = sorted(
            grouped.get(framework_id, []),
            key=lambda item: item["score"],
            reverse=True,
        )[:3]
        score = _support_score(
            [float(item["score"]) for item in ordered],
            CROSS_PROJECT_WEIGHTS,
        )
        output.append({
            "candidate_framework_result_id": f"CDR_{framework_id}",
            "framework_id": framework_id,
            "score": round(score, 6),
            "primary_project_framework_result_id": (
                ordered[0]["project_framework_result_id"] if ordered else None
            ),
            "supplemental_project_framework_result_ids": [
                item["project_framework_result_id"] for item in ordered[1:]
            ],
        })
    return output


def aggregate_resume_score(
    frameworks: list[dict[str, Any]],
    project_results: list[dict[str, Any]] | None = None,
) -> float:
    del project_results
    by_id = {
        item["framework_id"]: float(item["score"]) for item in frameworks
    }
    if not any(by_id.values()):
        return 0.0
    weighted_mean = sum(
        RESUME_FRAMEWORK_WEIGHTS[key] * by_id.get(key, 0.0)
        for key in RESUME_FRAMEWORK_WEIGHTS
    )
    score = (
        RESUME_BLEND_WEIGHTS[0] * max(by_id.values())
        + RESUME_BLEND_WEIGHTS[1] * weighted_mean
    )
    return round(min(1.0, score) * 100, 2)


def hierarchical_score(scores: list[float]) -> float:
    if not scores or not any(value > 0 for value in scores):
        return 0.0
    ordered = sorted(scores, reverse=True)
    if len(ordered) == 1:
        return min(1.0, ordered[0])
    active = sum(value > 0 for value in ordered)
    coverage = (active - 1) / (len(ordered) - 1)
    other_mean = sum(ordered[1:]) / (len(ordered) - 1)
    return min(
        1.0,
        FRAMEWORK_WEIGHTS[0] * ordered[0]
        + FRAMEWORK_WEIGHTS[1] * ordered[0] * coverage
        + FRAMEWORK_WEIGHTS[2] * other_mean,
    )


def _select_independent(
    items: list[dict[str, Any]],
    score_key: str,
) -> list[dict[str, Any]]:
    ordered = sorted(
        items,
        key=lambda item: float(item[score_key]),
        reverse=True,
    )
    selected, used = [], set()
    for item in ordered:
        footprint = {str(item.get("work_unit_id") or "")}
        if footprint & used:
            continue
        selected.append(item)
        used.update(footprint)
        if len(selected) == 3:
            break
    return selected


def _support_score(
    scores: list[float],
    weights: tuple[float, float],
) -> float:
    if not scores:
        return 0.0
    return min(
        1.0,
        scores[0]
        + (weights[0] * scores[1] if len(scores) > 1 else 0)
        + (weights[1] * scores[2] if len(scores) > 2 else 0),
    )
