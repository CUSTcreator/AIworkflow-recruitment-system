"""岗位草稿读模型：组装岗位草稿页面展示及人工修改标记。"""
from __future__ import annotations

import ast
import json
from typing import Any

from backend.app.models.entities import JobDraft
from backend.app.modules.jobs.job_recovery import job_recovery_actions
from backend.app.modules.document_ingestion.schemas.view_schemas import (
    JobDraftHardScreeningPreview,
    JobDraftExtractionAssistance,
    JobDraftFieldHint,
    JobDraftView,
)

def _text_list(value: object) -> list[str]:
    """兼容已落库的旧字符串列表；无论如何不把一个字符串拆成逐字数组。"""
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if not isinstance(value, str):
        return []
    text = value.strip()
    if not text:
        return []
    for parser in (json.loads, ast.literal_eval):
        try:
            parsed = parser(text)
        except (ValueError, SyntaxError, TypeError, json.JSONDecodeError):
            continue
        if isinstance(parsed, (list, tuple)):
            return [str(item).strip() for item in parsed if str(item).strip()]
    return []


_ALLOWED_FIELDS = {
    "title",
    "headcount",
    "responsibilities",
    "qualifications",
    "education_requirement",
    "major_requirement",
    "department_id",
}


def job_draft_view(draft: JobDraft) -> JobDraftView:
    fields = dict((draft.field_provenance_json or {}).get("fields") or {})
    hints: list[JobDraftFieldHint] = []
    for field, item in fields.items():
        if field not in _ALLOWED_FIELDS or not isinstance(item, dict):
            continue
        origin = str(item.get("origin") or "rule")
        if origin == "llm_repair":
            hints.append(JobDraftFieldHint(
                field=field,
                status="ai_assisted",
                message=str(item.get("message") or "AI根据原表内容提取，请核对"),
            ))
        elif origin == "unresolved":
            hints.append(JobDraftFieldHint(
                field=field,
                status="review_required",
                message=str(item.get("message") or "该字段需要人工确认"),
            ))
    overall = None
    if any(item.status == "review_required" for item in hints):
        overall = "review_required"
    elif hints:
        overall = "ai_assisted"
    assistance = JobDraftExtractionAssistance(status=overall, field_hints=hints) if overall else None
    # 分类 Step 已在发布草稿前完成；新草稿只投影持久化结果。迁移前旧草稿没有该
    # 字段时才使用确定性规则回退，不能在读请求中调用 LLM。
    preview_result = dict(draft.requirement_classification_json or {})
    if not preview_result:
        from recruitment_ai_core.job_capability.requirement_classification import deterministic_requirement_fallback
        preview_result = deterministic_requirement_fallback({
            "education_requirement": draft.education_requirement,
            "qualifications": _text_list(draft.qualifications),
        })
    hard_screening_preview = [
        JobDraftHardScreeningPreview(
            criterion_type=str(item.get("criterion_type") or "custom"),
            name=str(item.get("name") or "硬筛条件"),
            expected_value=item.get("expected_value"),
            description=str(item.get("description") or ""),
            # 用户保存后的启停状态属于确认输入，读模型不能重新开启已停用规则。
            enabled=bool(item.get("enabled", True)),
        )
        for item in list(preview_result.get("hardScreeningRules") or [])
        if item.get("expected_value") not in (None, "")
    ]
    action_codes: list[str] = []
    if draft.status == "draft":
        action_codes.append("edit_job_draft")
        if draft.department_match_status in {"ambiguous", "unmatched", "deleted", "inactive"} or not draft.department_id:
            action_codes.append("select_job_department")
        if draft.duplicate_status in {"existing_job", "same_upload"}:
            action_codes.append("resolve_duplicate_job")
        action_codes.extend(("confirm_job_draft", "skip_job_draft"))
    return JobDraftView(
        job_draft_id=draft.job_draft_id,
        source_document_id=draft.source_document_id,
        sequence_no=draft.sequence_no,
        title=draft.title,
        headcount=draft.headcount,
        responsibilities=_text_list(draft.responsibilities),
        qualifications=_text_list(draft.qualifications),
        education_requirement=draft.education_requirement,
        major_requirement=draft.major_requirement,
        department_id=draft.department_id,
        source_department_name=draft.source_department_name,
        department_match_status=draft.department_match_status,
        source_text=draft.source_text,
        status=draft.status,
        confirmed_job_id=draft.confirmed_job_id,
        duplicate_status=draft.duplicate_status or "new_job",
        existing_job_id=draft.existing_job_id,
        duplicate_group_id=draft.duplicate_group_id,
        resolution=draft.resolution or ("create" if not draft.existing_job_id else None),
        # SQLAlchemy 的列默认值在对象 flush 前尚不可见；读模型仍需保持合同完整。
        preset_model_id=draft.preset_model_id or "engineering_experience",
        preset_model_version=draft.preset_model_version or "1.0",
        recommended_preset_model_id=(draft.recommended_preset_model_id or draft.preset_model_id or "engineering_experience"),
        updated_at=draft.updated_at,
        extraction_assistance=assistance,
        hard_screening_preview=hard_screening_preview,
        available_actions=job_recovery_actions(action_codes),
    )


def unresolved_fields(draft: JobDraft) -> list[str]:
    fields: dict[str, Any] = dict((draft.field_provenance_json or {}).get("fields") or {})
    return [field for field, item in fields.items() if field in _ALLOWED_FIELDS and isinstance(item, dict) and item.get("origin") == "unresolved"]


def mark_user_override(draft: JobDraft, fields: set[str]) -> None:
    provenance = dict(draft.field_provenance_json or {})
    state_map = dict(provenance.get("fields") or {})
    for field in fields:
        if field in _ALLOWED_FIELDS and field in state_map:
            state_map[field] = {
                **dict(state_map[field] or {}),
                "origin": "user_override",
                "message": "",
            }
    provenance["fields"] = state_map
    draft.field_provenance_json = provenance


