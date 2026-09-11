from __future__ import annotations

"""从已发布的评分事实中选择少量、可追溯的优势信号。

这里不重新评分，也不生成自由文本；仅按证据独立性、证明层级和优先级挑选展示依据。
"""

from collections import defaultdict
from typing import Any


CROSS_PROJECT_WEIGHTS = (0.06, 0.04)
MAX_STRENGTHS = 4
_SCOPE_PRIORITY = {"aggregate": 0, "local": 1}
_PATTERN_PRIORITY = {"single_l5": 0, "single_l4": 1, "repeated_l3": 2}
_ROLE_PRIORITY = {"core": 0, "preset_indicator": 1, "supporting": 2}


def build_strength_signals(
    *,
    project_indicator_results: list[dict[str, Any]],
    work_unit_indicator_results: list[dict[str, Any]],
    job_capability_results: list[dict[str, Any]],
    pair_assessments: list[dict[str, Any]],
    job_capabilities: list[dict[str, Any]],
    max_items: int = MAX_STRENGTHS,
) -> list[dict[str, Any]]:
    """返回最多若干条优势信号，每条均保留对应评分结果 ID。"""
    candidates = [
        *_preset_candidates(project_indicator_results, work_unit_indicator_results),
        *_job_candidates(
            job_capability_results, pair_assessments, job_capabilities
        ),
    ]
    chosen = sorted(candidates, key=_sort_key)[: max(0, max_items)]
    return [
        {
            "strength_signal_id": f"STRENGTH_{index:02d}",
            "source_type": item["source_type"],
            "target_id": item["target_id"],
            "proof_scope": item["proof_scope"],
            "proof_pattern": item["proof_pattern"],
            "source_result_ids": item["source_result_ids"],
        }
        for index, item in enumerate(chosen, start=1)
    ]


def _preset_candidates(
    project_results: list[dict[str, Any]],
    work_unit_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    projects_by_indicator = _group(project_results, "indicator_id")
    units_by_indicator = _group(work_unit_results, "indicator_id")
    output: list[dict[str, Any]] = []
    for indicator_id in sorted(
        set(projects_by_indicator) | set(units_by_indicator)
    ):
        rows = _select_independent(
            projects_by_indicator.get(indicator_id, []),
            lambda row: (
                f"PROJECT:{row.get('project_id') or row.get('project_indicator_result_id')}"
            ),
        )
        pattern = _proof_pattern(rows, "level", repeated_count=2)
        scope = "aggregate"
        if pattern is None:
            rows = _select_independent(
                units_by_indicator.get(indicator_id, []),
                lambda row: (
                    f"WU:{row.get('project_id') or ''}:"
                    f"{row.get('work_unit_id') or row.get('work_unit_indicator_result_id')}"
                ),
            )
            pattern = _proof_pattern(rows, "level", repeated_count=3)
            scope = "local"
        if pattern is None:
            continue
        selected = _results_for_pattern(rows, pattern, "level")[:3]
        output.append({
            "source_type": "preset_indicator",
            "target_id": indicator_id,
            "proof_scope": scope,
            "proof_pattern": pattern,
            "source_result_ids": [
                str(
                    item["project_indicator_result_id"]
                    if scope == "aggregate"
                    else item["work_unit_indicator_result_id"]
                )
                for item in selected
            ],
            "role": "preset_indicator",
            "independent_proof_count": len(selected),
            "display_score": _support_score(
                [float(item.get("score") or 0) for item in selected]
            ),
        })
    return output


def _job_candidates(
    results: list[dict[str, Any]],
    pair_assessments: list[dict[str, Any]],
    job_capabilities: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    definitions = {
        str(item.get("job_capability_id")): item
        for item in job_capabilities
        if item.get("job_capability_id")
    }
    output: list[dict[str, Any]] = []
    for result in results:
        capability_id = str(result.get("job_capability_id") or "")
        result_id = str(result.get("job_capability_result_id") or "")
        if not capability_id or not result_id:
            continue
        selected_pairs = independent_job_pairs(result, pair_assessments)
        pattern = _proof_pattern(
            selected_pairs, "content_level", repeated_count=2
        )
        if pattern is None:
            continue
        proof_rows = _results_for_pattern(
            selected_pairs, pattern, "content_level"
        )
        output.append({
            "source_type": "job_capability",
            "target_id": capability_id,
            "proof_scope": "aggregate",
            "proof_pattern": pattern,
            "source_result_ids": [result_id],
            "role": str(
                definitions.get(capability_id, {}).get("role") or "supporting"
            ).lower(),
            "independent_proof_count": len(proof_rows),
            "display_score": float(result.get("score") or 0),
        })
    return output


def independent_job_pairs(
    result: dict[str, Any],
    pair_assessments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    pairs_by_id = {
        str(item.get("pair_id")): item
        for item in pair_assessments
        if item.get("pair_id")
    }
    rows = [
        pairs_by_id[pair_id]
        for pair_id in [
            result.get("primary_pair_id"),
            *list(result.get("supplemental_pair_ids") or []),
        ]
        if pair_id in pairs_by_id
        and int(pairs_by_id[pair_id].get("content_level") or 0) > 0
    ]
    return _select_independent(rows, _pair_footprint)


def _proof_pattern(
    rows: list[dict[str, Any]],
    level_key: str,
    *,
    repeated_count: int,
) -> str | None:
    levels = [int(item.get(level_key) or 0) for item in rows]
    if any(level >= 5 for level in levels):
        return "single_l5"
    if any(level >= 4 for level in levels):
        return "single_l4"
    if sum(level >= 3 for level in levels) >= repeated_count:
        return "repeated_l3"
    return None


def _results_for_pattern(rows, pattern: str, level_key: str):
    minimum = {"single_l5": 5, "single_l4": 4, "repeated_l3": 3}[pattern]
    return sorted(
        (item for item in rows if int(item.get(level_key) or 0) >= minimum),
        key=lambda item: (
            -int(item.get(level_key) or 0),
            -float(
                item.get("score", item.get("pair_score", 0)) or 0
            ),
        ),
    )


def _select_independent(rows, footprint_key):
    ordered = sorted(
        rows,
        key=lambda item: (
            -int(item.get("level", item.get("content_level", 0)) or 0),
            -float(
                item.get("score", item.get("pair_score", 0)) or 0
            ),
        ),
    )
    selected, used = [], set()
    for item in ordered:
        raw = footprint_key(item)
        footprint = {raw} if isinstance(raw, str) else set(raw)
        footprint.discard("")
        if footprint and footprint & used:
            continue
        selected.append(item)
        used.update(footprint)
    return selected


def _pair_footprint(item: dict[str, Any]) -> set[str]:
    work_units = {
        f"WU:{value}"
        for value in item.get("proof_work_unit_ids", [])
        if value
    }
    if work_units:
        return work_units
    return {
        f"{item.get('evidence_type')}:"
        f"{item.get('evidence_id') or item.get('pair_id')}"
    }


def _group(rows, key: str):
    output = defaultdict(list)
    for item in rows:
        identity = str(item.get(key) or "")
        if identity:
            output[identity].append(item)
    return output


def _support_score(scores: list[float]) -> float:
    if not scores:
        return 0.0
    return min(
        1.0,
        scores[0]
        + (
            CROSS_PROJECT_WEIGHTS[0] * scores[1]
            if len(scores) > 1
            else 0
        )
        + (
            CROSS_PROJECT_WEIGHTS[1] * scores[2]
            if len(scores) > 2
            else 0
        ),
    )


def _sort_key(item):
    return (
        _SCOPE_PRIORITY.get(item["proof_scope"], 9),
        _PATTERN_PRIORITY.get(item["proof_pattern"], 9),
        _ROLE_PRIORITY.get(item["role"], 9),
        -int(item["independent_proof_count"]),
        -float(item["display_score"]),
        item["target_id"],
    )
