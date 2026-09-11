"""V2/V3 共用的受影响结果选择辅助函数。"""
from __future__ import annotations

from typing import Any


def affected_result_refs(routes: list[dict[str, Any]]) -> dict[str, list[str]]:
    """按固定字段归集需要重算的能力、指标、风险和核验目标。"""
    return {
        "job_capability_ids": _collect(routes, "affected_job_capability_ids"),
        "preset_indicator_ids": _collect(routes, "affected_preset_indicator_ids"),
        "risk_ids": _collect(routes, "affected_risk_ids"),
        "interview_target_ids": _collect(routes, "affected_interview_target_ids"),
    }


def _collect(routes: list[dict[str, Any]], key: str) -> list[str]:
    return list(dict.fromkeys(str(item) for route in routes for item in route.get(key, []) if item))