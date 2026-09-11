"""从 FirstInterviewPlanVersion 组装一面题单确认页所需的展示模型。"""
from __future__ import annotations

from typing import Any

from backend.app.models.entities import FirstInterviewPlanVersion


def first_interview_plan_view(row: FirstInterviewPlanVersion) -> dict[str, Any]:
    """不重新规划，只将已发布版本的 ``presentation_json`` 映射成页面对象。

    页面展示只读取 draft_guide/question_suggestions 与版本元信息；不得从 core_result_json 或
    rule_result_json 临时拼装题目，避免前端看到未经展示层映射的 snake_case 内部字段。
    """
    presentation = _mapping(row.presentation_json)
    guide = _mapping(presentation.get("draft_guide"))
    return {
        **guide,
        "planVersionId": row.plan_version_id,
        "planVersion": row.version,
        "planStatus": row.status,
        "sourceAssessmentVersionId": row.source_assessment_version_id,
        "questionSuggestions": _items(presentation.get("question_suggestions"))
        or _items(guide.get("questionSuggestions")),
        "planningSummary": presentation.get("planning_summary") or guide.get("summary") or "",
        "generationMode": presentation.get("generation_mode") or "rule_fallback",
        "generationWarnings": _strings(presentation.get("generation_warnings")),
        "generatedAt": presentation.get("generated_at") or (
            row.published_at.isoformat() if row.published_at else row.created_at.isoformat()
        ),
    }


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _items(value: Any) -> list[dict[str, Any]]:
    return [dict(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _strings(value: Any) -> list[str]:
    return [str(item) for item in value if str(item)] if isinstance(value, list) else []
