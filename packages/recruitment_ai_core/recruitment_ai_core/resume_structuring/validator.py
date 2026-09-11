from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Literal

from recruitment_ai_core.screening_scoring.contracts import VerifiedResumeIR
from recruitment_ai_core.screening_scoring.resume_structurer import (
    _contains_date_range,
    _is_explicit_duty_text,
    _project_title_error_code,
)


RepairScope = Literal["experience_section", "whole_resume_outline"]

_FIELD_LABELS = {
    "项目概述",
    "项目简介",
    "项目介绍",
    "项目描述",
    "技术栈",
    "开发环境",
    "工作内容",
    "主要工作",
    "核心工作",
    "主要贡献",
    "技术亮点",
    "项目成果",
    "性能优化",
    "架构设计",
    "职责描述",
}


@dataclass(slots=True)
class StructureValidation:
    accepted: bool
    errors: list[dict[str, Any]]
    warnings: list[str]
    repair_scope: RepairScope | None


@dataclass(slots=True)
class EvidenceCoverageValidation:
    accepted: bool
    errors: list[str]
    warnings: list[str]


def validate_resume_structure(
    resume_ir: VerifiedResumeIR,
    blocks: list[dict[str, Any]],
) -> StructureValidation:
    errors: list[dict[str, Any]] = []
    warnings = list(
        resume_ir.input_quality_report.warnings
        if resume_ir.input_quality_report is not None
        else []
    )
    block_ids = [str(item.get("block_id") or "") for item in blocks]
    if len(block_ids) != len(set(block_ids)) or any(not item for item in block_ids):
        errors.append(_error("source_block_ids_invalid", "whole_resume_outline"))

    # resolve_fact_kind intentionally treats ambiguous short lines as title
    # candidates.  Only spans that the outline actually made into project
    # titles participate in project-boundary validation.
    title_spans = [
        item
        for item in resume_ir.candidate_spans
        if (
            item.section == "experience"
            and item.rough_type == "experience_title"
            and item.experience_unit_id is None
        )
    ]
    # A resume may legitimately contain only education/skills, or only project
    # background without a scorable action. Missing experience is therefore a
    # quality signal, not corrupt evidence and must not block the submission.
    if not resume_ir.experience_units:
        warnings.append("experience_units_missing")
    elif not resume_ir.source_bullets:
        warnings.append("scorable_experience_missing")

    for title in title_spans:
        label = title.text.split("：", 1)[0].split(":", 1)[0].strip()
        if label in _FIELD_LABELS:
            errors.append(
                _error(
                    "project_title_is_field_label",
                    "experience_section",
                    source_block_ids=title.source_block_ids,
                )
            )
        title_error = _project_title_error_code(title.text)
        if title_error and not _is_valid_placeholder_boundary(
            title, resume_ir.candidate_spans
        ):
            errors.append(
                _error(
                    title_error,
                    "experience_section",
                    source_block_ids=title.source_block_ids,
                )
            )

    ordered = sorted(
        title_spans,
        key=lambda item: (item.source_line_start, item.subspan_index),
    )
    for previous, current in zip(ordered, ordered[1:]):
        # 不能仅因两个项目标题都存在就判定前一项目没有内容。正常简历中，项目
        # 标题不会附着 experience_unit_id，介于两个标题之间的简介、技术栈和要点
        # 才会被归属到前一项目。旧实现忽略了这些中间 Span，会把多项目简历普遍
        # 误判为 project_without_content。该信号只保留为告警，不触发强制修复。
        has_content_between = any(
            item.section == "experience"
            and item.experience_unit_id is not None
            and previous.source_line_start < item.source_line_start < current.source_line_start
            and bool(item.text.strip())
            for item in resume_ir.candidate_spans
        )
        if not has_content_between:
            warnings.append("project_without_content")

    bullet_counts = [
        len(item.source_bullet_ids) for item in resume_ir.experience_units
    ]
    if len(bullet_counts) >= 2:
        one_line_ratio = sum(value <= 1 for value in bullet_counts) / len(bullet_counts)
        if one_line_ratio >= 0.60:
            warnings.append("too_many_single_line_projects")

    # A deterministic outline can still merge several table-form projects.
    # Preserve that fact as a visible warning. The source blocks remain usable,
    # so this heuristic alone must never turn a deliverable submission into a
    # technical failure.
    for unit in resume_ir.experience_units:
        unit_spans = [
            span
            for span in resume_ir.candidate_spans
            if span.experience_unit_id == unit.experience_unit_id
        ]
        date_rows = [
            span
            for span in unit_spans
            if _contains_date_range(span.text)
            and ("|" in span.text or "｜" in span.text)
        ]
        description_rows = [
            span
            for span in unit_spans
            if re.match(
                r"^\s*(?:项目描述|项目简介|项目介绍|项目概述)\s*[：:]?",
                span.text,
            )
        ]
        generic_title = unit.title.strip() in {"项目经历", "主要项目经历"}
        if len(date_rows) >= 2 and len(description_rows) >= 2:
            warnings.append("experience_units_overmerged")
        elif generic_title and len(description_rows) >= 2:
            warnings.append("experience_units_overmerged")

    # 启发式信号不等同于“原文证据损坏”。它们保留给页面和日志，只有来源 ID
    # 冲突、伪造标题等可客观验证且无法发布的错误才触发修复或阻断。

    unique_errors: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in errors:
        key = (item["code"], item["scope"])
        if key not in seen:
            seen.add(key)
            unique_errors.append(item)
    scope: RepairScope | None = None
    if unique_errors:
        scope = (
            "whole_resume_outline"
            if any(item["scope"] == "whole_resume_outline" for item in unique_errors)
            else "experience_section"
        )
    return StructureValidation(
        accepted=not unique_errors,
        errors=unique_errors,
        warnings=list(dict.fromkeys(warnings)),
        repair_scope=scope,
    )


def _error(code: str, scope: RepairScope, **details: Any) -> dict[str, Any]:
    return {"code": code, "scope": scope, "details": details}


def _is_valid_placeholder_boundary(title: Any, spans: list[Any]) -> bool:
    """A date/table row is valid when it introduces a labelled project block."""
    text = str(getattr(title, "text", "") or "")
    compact = re.sub(r"\s+", "", text)
    if "|" not in compact and "｜" not in compact:
        return False
    if not re.search(r"(?:19|20)\d{2}", compact):
        return False
    meaningful = re.sub(r"[|｜\-_—–~～﹣－至到今0-9./年月个()（）]", "", compact)
    if meaningful:
        return False
    following = sorted(
        (
            span
            for span in spans
            if getattr(span, "section", None) == "experience"
            and getattr(span, "source_line_start", 0) > getattr(title, "source_line_start", 0)
        ),
        key=lambda span: (
            getattr(span, "source_line_start", 0),
            getattr(span, "subspan_index", 0),
        ),
    )
    if not following:
        return False
    next_text = str(getattr(following[0], "text", "") or "")
    return bool(
        re.match(
            r"^\s*(?:项目描述|项目简介|项目介绍|项目概述|项目名称)\s*[：:]?",
            next_text,
        )
    )


def validate_resume_evidence_coverage(
    resume_ir: VerifiedResumeIR,
) -> EvidenceCoverageValidation:
    """Validate downstream evidence after WorkUnit and Skill activities finish."""
    errors: list[str] = []
    warnings: list[str] = []
    bullets = {item.source_bullet_id: item for item in resume_ir.source_bullets}
    bullet_keys = {
        (item.experience_unit_id, item.source_line_start, item.source_line_end, item.raw_text)
        for item in resume_ir.source_bullets
    }
    for span in resume_ir.candidate_spans:
        if not span.experience_unit_id or not _is_explicit_duty_text(span.text):
            continue
        key = (
            span.experience_unit_id,
            span.source_line_start,
            span.source_line_end,
            span.text,
        )
        if key not in bullet_keys:
            errors.append(f"explicit_duty_missing_source_bullet:{span.span_id}")

    referenced: set[str] = set()
    for unit in resume_ir.scorable_work_units:
        unit_refs = [str(item.get("bullet_id") or "") for item in unit.source_refs]
        if not unit_refs or any(item not in bullets for item in unit_refs):
            errors.append(f"work_unit_invalid_source_refs:{unit.work_unit_id}")
            continue
        referenced.update(unit_refs)
    for bullet in resume_ir.source_bullets:
        if _is_explicit_duty_text(bullet.raw_text) and bullet.source_bullet_id not in referenced:
            errors.append(
                f"explicit_duty_missing_work_unit:{bullet.source_bullet_id}"
            )
    if resume_ir.source_bullets and not resume_ir.scorable_work_units:
        warnings.append("source_bullets_without_work_units")
    return EvidenceCoverageValidation(
        accepted=not errors,
        errors=list(dict.fromkeys(errors)),
        warnings=list(dict.fromkeys(warnings)),
    )
