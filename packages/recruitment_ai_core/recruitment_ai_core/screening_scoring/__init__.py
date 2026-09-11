"""初筛评分算法包的公开入口。"""
from typing import Any


def run_screening_scoring(payload: Any) -> dict[str, Any]:
    from .pipeline import run_screening_scoring as _run
    return _run(payload)


def score_resume_evidence(payload: Any) -> dict[str, Any]:
    from .pipeline import score_resume_evidence as _run
    return _run(payload)


def score_job_capabilities(payload: Any, experience: dict[str, Any]) -> dict[str, Any]:
    from .pipeline import score_job_capabilities as _run
    return _run(payload, experience)


def assemble_screening_core(payload: Any, experience: dict[str, Any], job_result: dict[str, Any]) -> dict[str, Any]:
    from .pipeline import assemble_screening_core as _run
    return _run(payload, experience, job_result)


__all__ = [
    "run_screening_scoring", "score_resume_evidence", "score_job_capabilities", "assemble_screening_core",
]
