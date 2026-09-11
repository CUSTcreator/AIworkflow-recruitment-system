"""岗位画像能否作为 V1 冻结来源的统一判定。

数据库中存在画像记录或 ``profile_status=ready`` 只表示历史流程曾经发布过它，
不等于画像包含可评分能力。所有进入 V1 的入口都必须复用这里的内容校验，避免
历史空画像绕过上游检查后，直到 ``freeze_screening_sources`` 才阻塞申请。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class JobProfileScreeningReadiness:
    ready: bool
    error_code: str = ""
    missing_required_unit_ids: tuple[str, ...] = ()


def evaluate_job_profile_json(
    profile_json: Mapping[str, Any] | None,
) -> JobProfileScreeningReadiness:
    """校验 V1 真正依赖的最小岗位能力集合及 required JDUnit 覆盖。"""

    payload = dict(profile_json or {})
    capabilities = [
        item
        for item in list(payload.get("job_capabilities") or [])
        if isinstance(item, dict)
    ]
    if not capabilities:
        return JobProfileScreeningReadiness(
            ready=False,
            error_code="screening_job_capabilities_missing",
        )

    required_unit_ids = {
        str(item.get("job_unit_id") or "").strip()
        for item in list(payload.get("jd_units") or [])
        if isinstance(item, dict)
        and str(item.get("scoring_role") or "") == "required"
        and str(item.get("job_unit_id") or "").strip()
    }
    covered_unit_ids = {
        str(item.get("job_unit_id") or "").strip()
        for item in capabilities
        if str(item.get("job_unit_id") or "").strip()
    }
    missing = tuple(sorted(required_unit_ids - covered_unit_ids))
    if missing:
        return JobProfileScreeningReadiness(
            ready=False,
            error_code="screening_job_capability_coverage_missing",
            missing_required_unit_ids=missing,
        )
    return JobProfileScreeningReadiness(ready=True)


def evaluate_job_profile_record(
    profile: Any | None,
    *,
    job_id: str,
    jd_version_id: str,
) -> JobProfileScreeningReadiness:
    """同时校验画像外键绑定与内容；可接受 ORM 记录或测试替身。"""

    if profile is None:
        return JobProfileScreeningReadiness(
            ready=False,
            error_code="application_frozen_job_profile_missing",
        )
    if (
        str(getattr(profile, "job_id", "") or "") != str(job_id)
        or str(getattr(profile, "jd_version_id", "") or "") != str(jd_version_id)
    ):
        return JobProfileScreeningReadiness(
            ready=False,
            error_code="job_profile_version_mismatch",
        )
    return evaluate_job_profile_json(getattr(profile, "profile_json", None))
