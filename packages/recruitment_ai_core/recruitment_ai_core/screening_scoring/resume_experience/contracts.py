from __future__ import annotations

from typing import Any, Literal, TypedDict


LEVEL_STRENGTH = {0: 0.0, 1: 0.35, 2: 0.60, 3: 0.77, 4: 0.87, 5: 1.0}


class IndicatorResult(TypedDict, total=False):
    work_unit_indicator_result_id: str
    project_id: str
    work_unit_id: str
    indicator_id: str
    level: int
    score: float

    reason: str


class ResumeExperienceError(RuntimeError):
    pass
