from __future__ import annotations

"""将 LLM 修复结果装配回确定性简历骨架。

这里是“LLM 可以重组、不能凭空新增事实”的最后边界：所有引用 ID 必须存在，
并继续保留原文区块、项目上下文和输入质量信息。
"""

import hashlib
from typing import Any

from recruitment_ai_core.screening_scoring.contracts import (
    CandidateSpan,
    ExperienceUnit,
    ProjectContextItem,
    ScorableWorkUnit,
    SourceBullet,
    VerifiedResumeIR,
)
from recruitment_ai_core.screening_scoring.fact_kind import resolve_fact_kind
from recruitment_ai_core.screening_scoring.resume_input_quality import (
    evaluate_resume_input_quality,
)
from recruitment_ai_core.screening_scoring.resume_structurer import (
    _context_type,
    _derived_title_from_context,
    _is_explicit_duty_text,
    _is_placeholder_date_row,
    _is_scorable_experience_text,
    _project_title_error_code,
    _redact,
)

from .validator import _FIELD_LABELS


_EDUCATION_FIELD_LABELS = {
    "学校",
    "学校名称",
    "学院",
    "学院名称",
    "教育经历",
    "教育背景",
    "学习经历",
    "专业",
    "所学专业",
    "学历",
    "学位",
    "最高学历",
    "学习形式",
}


def assemble_repaired_ir(
    *,
    candidate_id: str,
    resume_text: str,
    blocks: list[dict[str, Any]],
    payload: dict[str, Any],
    deterministic_ir: VerifiedResumeIR,
    repair_trace: list[dict[str, Any]],
) -> VerifiedResumeIR:
    """校验修复结果后生成正式 Resume IR，供后续评分使用。"""
    ordered_blocks = sorted(blocks, key=lambda item: int(item.get("order") or 0))
    by_id = {str(item.get("block_id") or ""): item for item in ordered_blocks}

    deterministic_sections: dict[str, str] = {}
    for span in deterministic_ir.candidate_spans:
        for block_id in span.source_block_ids:
            deterministic_sections.setdefault(block_id, span.section)

    protected_education_ids = {
        block_id
        for block_id, section in deterministic_sections.items()
        if section == "education"
    }
    protected_education_ids.update(
        str(block.get("block_id") or "")
        for block in ordered_blocks
        if str(block.get("section") or "") == "education"
        or str(block.get("field_label") or "").strip() in _EDUCATION_FIELD_LABELS
    )

    # The repair schema asks the model for a title, but a model may select a
    # school/degree block as that title. Remove protected education blocks
    # before validation; a titleless project can still retain its real content
    # and will receive a deterministic fallback title below.
    normalized_projects: list[dict[str, list[str]]] = []
    for project in payload.get("projects", []):
        title_ids = [
            str(item)
            for item in project.get("title_block_ids", [])
            if str(item) not in protected_education_ids
        ]
        content_ids = [
            str(item)
            for item in project.get("content_block_ids", [])
            if str(item) not in protected_education_ids
        ]
        if content_ids:
            normalized_projects.append({
                "title_block_ids": title_ids,
                "content_block_ids": content_ids,
            })
    payload = {**payload, "projects": normalized_projects}

    # A protected block that was only referenced by a removed project still
    # needs an in-scope assignment so the repair coverage contract remains
    # complete. It will be emitted as an education span, never as project data.
    scope_ids = {
        str(item)
        for item in payload.get("_repair_scope_block_ids", [])
        if str(item)
    }
    referenced_ids = {
        str(item)
        for section in payload.get("sections", [])
        for item in section.get("block_ids", [])
    }
    referenced_ids.update(
        str(item)
        for project in payload.get("projects", [])
        for key in ("title_block_ids", "content_block_ids")
        for item in project.get(key, [])
    )
    unresolved_ids = [str(item) for item in payload.get("unresolved_block_ids", [])]
    unresolved_ids.extend(
        block_id
        for block_id in sorted(protected_education_ids & scope_ids)
        if block_id not in referenced_ids and block_id not in unresolved_ids
    )
    payload["unresolved_block_ids"] = unresolved_ids

    _validate_payload_ids(
        payload,
        by_id,
        allow_titleless_projects=True,
    )

    section_by_block = dict(deterministic_sections)
    for section in payload.get("sections", []):
        section_type = str(section.get("section_type") or "other")
        for block_id in section.get("block_ids", []):
            block_key = str(block_id)
            if block_key in protected_education_ids and section_type != "education":
                continue
            section_by_block[block_key] = section_type

    project_title_ids: dict[str, str] = {}
    project_content_ids: dict[str, str] = {}
    projects: list[dict[str, Any]] = []
    for index, project in enumerate(payload.get("projects", []), start=1):
        project_id = f"EXP_{index:03d}"
        title_ids = [str(item) for item in project.get("title_block_ids", [])]
        content_ids = [str(item) for item in project.get("content_block_ids", [])]
        if not content_ids:
            raise ValueError("resume_structure_project_requires_title_and_content")
        for block_id in title_ids:
            if block_id in project_title_ids or block_id in project_content_ids:
                raise ValueError("resume_structure_duplicate_project_block")
            project_title_ids[block_id] = project_id
        for block_id in content_ids:
            if block_id in project_title_ids or block_id in project_content_ids:
                raise ValueError("resume_structure_duplicate_project_block")
            project_content_ids[block_id] = project_id
        projects.append(
            {
                "project_id": project_id,
                "title_ids": title_ids,
                "content_ids": content_ids,
            }
        )

    spans: list[CandidateSpan] = []
    section_headers_found: list[str] = []
    for index, block in enumerate(ordered_blocks, start=1):
        block_id = str(block.get("block_id") or "")
        text = str(block.get("text") or "").strip()
        if not text:
            continue
        project_id = project_content_ids.get(block_id)
        is_title = block_id in project_title_ids
        section = "experience" if is_title or project_id else section_by_block.get(block_id, "other")
        if section in {"experience", "skills", "education"}:
            section_headers_found.append(section)
        spans.append(
            CandidateSpan(
                span_id=f"SPAN_{len(spans) + 1:03d}",
                section=section,
                rough_type="experience_title" if is_title else resolve_fact_kind(text),
                experience_unit_id=project_id,
                source_line_start=index,
                source_line_end=index,
                subspan_index=index,
                text=text,
                source_block_ids=[block_id],
            )
        )

    source_bullets: list[SourceBullet] = []
    for span in spans:
        if not span.experience_unit_id or not _is_scorable_experience_text(span.text):
            continue
        source_bullet_id = f"SB_{len(source_bullets) + 1:03d}"
        source_bullets.append(
            SourceBullet(
                source_bullet_id=source_bullet_id,
                experience_unit_id=span.experience_unit_id,
                raw_text=span.text,
                source_line_start=span.source_line_start,
                source_line_end=span.source_line_end,
                work_unit_ids=[],
                source_block_ids=list(span.source_block_ids),
            )
        )

    units: list[ExperienceUnit] = []
    context_items: list[ProjectContextItem] = []
    span_by_block = {
        span.source_block_ids[0]: span for span in spans if span.source_block_ids
    }
    for project in projects:
        project_id = project["project_id"]
        raw_title = " ".join(
            str(by_id[item].get("text") or "").strip()
            for item in project["title_ids"]
        ).strip()
        title = _display_project_title(
            raw_title,
            [
                str(by_id[item].get("text") or "").strip()
                for item in project["content_ids"]
            ],
        )
        content_spans = [
            span_by_block[item]
            for item in project["content_ids"]
            if item in span_by_block
        ]
        units.append(
            ExperienceUnit(
                experience_unit_id=project_id,
                title=title or project_id,
                span_ids=[item.span_id for item in content_spans],
                source_bullet_ids=[
                    item.source_bullet_id
                    for item in source_bullets
                    if item.experience_unit_id == project_id
                ],
            )
        )
        for title_id in project["title_ids"]:
            title_span = span_by_block.get(title_id)
            if title_span:
                context_items.append(
                    ProjectContextItem(
                        context_id=f"CTX_{len(context_items) + 1:03d}",
                        experience_unit_id=project_id,
                        context_type="project_title",
                        text=title_span.text,
                        source_line_start=title_span.source_line_start,
                        source_line_end=title_span.source_line_end,
                        source_block_ids=[title_id],
                    )
                )
        scorable_blocks = {
            block_id
            for item in source_bullets
            if item.experience_unit_id == project_id
            for block_id in item.source_block_ids
        }
        for content_id in project["content_ids"]:
            if content_id in scorable_blocks:
                continue
            span = span_by_block.get(content_id)
            if span:
                context_items.append(
                    ProjectContextItem(
                        context_id=f"CTX_{len(context_items) + 1:03d}",
                        experience_unit_id=project_id,
                        context_type=_context_type(span.text),
                        text=span.text,
                        source_line_start=span.source_line_start,
                        source_line_end=span.source_line_end,
                        source_block_ids=[content_id],
                    )
                )

    redacted = _redact(resume_text)
    report = evaluate_resume_input_quality(
        redacted,
        spans=spans,
        experience_units=units,
        source_bullets=source_bullets,
        section_headers_found=section_headers_found,
    )
    return VerifiedResumeIR(
        resume_ir_version="verified_resume_ir_v1_4",
        candidate_id=candidate_id,
        resume_raw_sha256=hashlib.sha256(resume_text.encode("utf-8")).hexdigest(),
        resume_redacted_sha256=hashlib.sha256(redacted.encode("utf-8")).hexdigest(),
        candidate_spans=spans,
        experience_units=units,
        source_bullets=source_bullets,
        scorable_work_units=[],
        project_context_items=context_items,
        input_quality_report=report,
        structuring_provenance={
            "mode": "llm_structure_repair_v1",
            "source_block_count": len(ordered_blocks),
            "repair_trace": repair_trace,
        },
    )


def _validate_payload_ids(
    payload: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
    *,
    allow_titleless_projects: bool = False,
) -> None:
    valid_ids = set(by_id)
    section_references: list[str] = []
    for section in payload.get("sections", []):
        section_references.extend(str(item) for item in section.get("block_ids", []))
    project_references: list[str] = []
    for project in payload.get("projects", []):
        project_references.extend(
            str(item) for item in project.get("title_block_ids", [])
        )
        project_references.extend(
            str(item) for item in project.get("content_block_ids", [])
        )
    unresolved = [
        str(item) for item in payload.get("unresolved_block_ids", [])
    ]
    referenced = [*section_references, *project_references, *unresolved]
    unknown = sorted(set(referenced) - valid_ids)
    if unknown:
        raise ValueError(f"resume_structure_unknown_block_ids:{','.join(unknown[:10])}")
    allowed_ids = {
        str(item)
        for item in payload.get("_repair_scope_block_ids", [])
        if str(item)
    }
    outside_scope = sorted(set(referenced) - allowed_ids) if allowed_ids else []
    if outside_scope:
        raise ValueError(
            f"resume_structure_outside_repair_scope:{','.join(outside_scope[:10])}"
        )
    if allowed_ids:
        missing = sorted(allowed_ids - set(referenced))
        if missing:
            raise ValueError(
                f"resume_structure_repair_scope_blocks_missing:{','.join(missing[:10])}"
            )
    if len(section_references) != len(set(section_references)):
        raise ValueError("resume_structure_duplicate_section_block")
    if len(project_references) != len(set(project_references)):
        raise ValueError("resume_structure_duplicate_project_block")
    if len(unresolved) != len(set(unresolved)):
        raise ValueError("resume_structure_duplicate_unresolved_block")
    if set(unresolved) & set(project_references):
        raise ValueError("resume_structure_unresolved_project_overlap")
    if set(unresolved) & set(section_references):
        raise ValueError("resume_structure_unresolved_section_overlap")

    def positions(block_ids: list[str]) -> list[int]:
        return [int(by_id[item].get("order") or 0) for item in block_ids]

    def ensure_in_source_order(block_ids: list[str], error_code: str) -> None:
        values = positions(block_ids)
        if values != sorted(values):
            raise ValueError(error_code)

    for section in payload.get("sections", []):
        ensure_in_source_order(
            [str(item) for item in section.get("block_ids", [])],
            "resume_structure_section_blocks_out_of_order",
        )
    ensure_in_source_order(unresolved, "resume_structure_unresolved_blocks_out_of_order")

    previous_project_end = -1
    for project in payload.get("projects", []):
        title_ids = [str(item) for item in project.get("title_block_ids", [])]
        content_ids = [str(item) for item in project.get("content_block_ids", [])]
        if not title_ids and not allow_titleless_projects:
            raise ValueError("resume_structure_project_requires_title_and_content")
        if not content_ids:
            raise ValueError("resume_structure_project_requires_title_and_content")
        ensure_in_source_order(title_ids, "resume_structure_project_title_blocks_out_of_order")
        ensure_in_source_order(content_ids, "resume_structure_project_content_blocks_out_of_order")
        title_positions = positions(title_ids)
        content_positions = positions(content_ids)
        if title_positions and max(title_positions) >= min(content_positions):
            raise ValueError("resume_structure_project_title_must_precede_content")
        project_start = min([*title_positions, *content_positions])
        project_end = max([*title_positions, *content_positions])
        if project_start <= previous_project_end:
            raise ValueError("resume_structure_projects_out_of_order")
        previous_project_end = project_end

        for block_id in title_ids:
            text = str(by_id[block_id].get("text") or "").strip()
            label = text.split("：", 1)[0].split(":", 1)[0].strip()
            if label in _FIELD_LABELS:
                raise ValueError("resume_structure_project_title_is_field_label")
            title_error = _project_title_error_code(text)
            placeholder_boundary = (
                _is_placeholder_date_row(text)
                and bool(content_ids)
                and _derived_title_from_context(
                    str(by_id[content_ids[0]].get("text") or "")
                ) is not None
            )
            if title_error and not placeholder_boundary:
                raise ValueError(f"resume_structure_{title_error}")


def _display_project_title(raw_title: str, content_texts: list[str]) -> str:
    """Use an exact description prefix when a table row has no readable name."""
    if raw_title and not _is_placeholder_date_row(raw_title):
        if _project_title_error_code(raw_title) is None:
            return raw_title
    for text in content_texts:
        derived = _derived_title_from_context(text)
        if derived:
            return derived
    # If the only model-selected title was protected education text, retain a
    # short non-duty content line as a source-backed display title instead of
    # exposing an internal EXP_* identifier to the user.
    for text in content_texts:
        candidate = str(text or "").strip()
        if (
            candidate
            and len(candidate) <= 80
            and not _is_explicit_duty_text(candidate)
            and _project_title_error_code(candidate) is None
        ):
            return candidate
    return raw_title
