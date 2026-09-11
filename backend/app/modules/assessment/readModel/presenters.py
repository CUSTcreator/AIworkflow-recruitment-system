from __future__ import annotations

from copy import deepcopy
from typing import Any

from backend.app.shared.errors import BusinessError


CURRENT_SCREENING_VIEW_VERSION = "screening_result_view_v2_0"


def screening_summary_view(
    assessment: dict[str, Any] | None,
) -> dict[str, Any]:
    if not assessment:
        raise BusinessError(
            "screening_not_ready",
            "初步筛选结果尚未生成",
            status_code=409,
        )
    result_view = assessment.get("screeningResultView")
    if isinstance(result_view, dict):
        version = result_view.get("viewSchemaVersion")
        if version != CURRENT_SCREENING_VIEW_VERSION:
            raise BusinessError(
                "screening_view_version_unsupported",
                "该任务使用历史版初步筛选结果，请重新运行初步筛选或归档该任务",
                status_code=409,
            )
    page_assessment = deepcopy(assessment)
    result_view = page_assessment.get("screeningResultView")
    if isinstance(result_view, dict):
        _normalize_resume_capability_frameworks(result_view)
    embedded_summary = page_assessment.get("aiDecisionSummary")
    if isinstance(embedded_summary, dict):
        embedded_summary.pop("trace", None)
        embedded_summary.pop("warning", None)
    _strip_raw_text(page_assessment)
    return page_assessment


def _normalize_resume_capability_frameworks(result_view: dict[str, Any]) -> None:
    """Normalize persisted pre-contract capability-domain keys at the read boundary."""
    frameworks = result_view.get("resumeCapabilities")
    if not isinstance(frameworks, list):
        return
    for framework in frameworks:
        if not isinstance(framework, dict):
            continue
        if not framework.get("frameworkId") and framework.get("domainId"):
            framework["frameworkId"] = framework["domainId"]
        if not framework.get("frameworkName") and framework.get("domainName"):
            framework["frameworkName"] = framework["domainName"]


def _strip_raw_text(value: Any) -> None:
    if isinstance(value, dict):
        value.pop("rawText", None)
        for nested in value.values():
            _strip_raw_text(nested)
    elif isinstance(value, list):
        for nested in value:
            _strip_raw_text(nested)
