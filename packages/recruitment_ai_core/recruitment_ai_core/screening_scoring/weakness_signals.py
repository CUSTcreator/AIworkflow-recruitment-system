from __future__ import annotations

"""从评分结果中选择需要核实的薄弱项。

薄弱项表示“当前简历证明不足”，不等价于候选人不具备该能力；它们会转化为面试考察目标。
"""

from collections import defaultdict
from typing import Any

from .strength_signals import independent_job_pairs


MAX_WEAKNESSES = 4
_ROLE_PRIORITY = {"core": 0, "preset_indicator": 1, "supporting": 2}


def build_weakness_signals(
    *,
    project_indicator_results: list[dict[str, Any]],
    job_capability_results: list[dict[str, Any]],
    pair_assessments: list[dict[str, Any]],
    job_capabilities: list[dict[str, Any]],
    max_items: int = MAX_WEAKNESSES,
) -> list[dict[str, Any]]:
    """返回有限且可追溯的证据缺口，避免页面展示所有低分项。"""
    candidates = [
        *_preset_candidates(project_indicator_results),
        *_job_candidates(
            job_capability_results, pair_assessments, job_capabilities
        ),
    ]
    ordered = sorted(
        candidates,
        key=lambda item: (
            _ROLE_PRIORITY.get(item["role"], 9),
            -item["independent_proof_count"],
            item["display_score"],
            item["target_id"],
        ),
    )[: max(0, max_items)]
    return [
        {
            "weakness_signal_id": f"WEAKNESS_{index:02d}",
            "source_type": item["source_type"],
            "target_id": item["target_id"],
            "source_result_ids": item["source_result_ids"],
        }
        for index, item in enumerate(ordered, start=1)
    ]


def _preset_candidates(rows):
    grouped = defaultdict(list)
    for item in rows:
        if int(item.get("level") or 0) == 1 and item.get("indicator_id"):
            grouped[str(item["indicator_id"])].append(item)
    output = []
    for indicator_id, values in grouped.items():
        values.sort(key=lambda item: float(item.get("score") or 0))
        output.append({
            "source_type": "preset_indicator",
            "target_id": indicator_id,
            "source_result_ids": [
                str(item["project_indicator_result_id"])
                for item in values
                if item.get("project_indicator_result_id")
            ],
            "role": "preset_indicator",
            "independent_proof_count": len({
                str(
                    item.get("project_id")
                    or item.get("project_indicator_result_id")
                )
                for item in values
            }),
            "display_score": min(
                float(item.get("score") or 0) for item in values
            ),
        })
    return output


def _job_candidates(results, pairs, definitions):
    by_id = {
        str(item.get("job_capability_id")): item
        for item in definitions
        if item.get("job_capability_id")
    }
    output = []
    for result in results:
        capability_id = str(result.get("job_capability_id") or "")
        result_id = str(result.get("job_capability_result_id") or "")
        selected = independent_job_pairs(result, pairs)
        if not capability_id or not result_id or not selected:
            continue
        if max(
            int(item.get("content_level") or 0) for item in selected
        ) != 1:
            continue
        output.append({
            "source_type": "job_capability",
            "target_id": capability_id,
            "source_result_ids": [result_id],
            "role": str(
                by_id.get(capability_id, {}).get("role") or "supporting"
            ).lower(),
            "independent_proof_count": len(selected),
            "display_score": float(result.get("score") or 0),
        })
    return output
