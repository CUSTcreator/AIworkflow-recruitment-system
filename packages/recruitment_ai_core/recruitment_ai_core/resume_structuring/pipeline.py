from __future__ import annotations

"""简历结构化总入口：先用确定性规则建立骨架，再用 LLM 仅修复语义结构。

输出的结构化简历是后续 WorkUnit、SkillClaim 与初筛计算的共同事实来源；
本模块不产生能力分，也不补写原文没有表达的经历。
"""

from dataclasses import asdict
import re
from statistics import median
from typing import Any

from recruitment_ai_core.screening_scoring.contracts import (
    CandidateSpan,
    ExperienceUnit,
    ProjectContextItem,
    ResumeInputQualityReport,
    ScorableWorkUnit,
    SourceBullet,
    VerifiedResumeIR,
)
from recruitment_ai_core.screening_scoring.resume_structurer import build_resume_ir
from recruitment_ai_core.screening_scoring.section_resolver import section_by_line

from .assembler import assemble_repaired_ir
from .llm_repair import repair_resume_structure
from .skill_claim_extractor import extract_skill_claims
from .logical_block_normalizer import expand_logical_block, source_block_id
from .validator import StructureValidation, validate_resume_structure
from .work_unit_extractor import extract_work_units


class ResumeStructureReviewRequired(RuntimeError):
    def __init__(self, result: dict[str, Any]):
        self.result = result
        super().__init__("resume_structure_review_required")


def structure_resume(
    *,
    candidate_id: str,
    resume_text: str,
    document_blocks: list[dict[str, Any]] | None,
    llm_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """将解析后的简历文本转换为可评分的、可追溯的结构化简历。"""
    blocks = _normalize_blocks(document_blocks, resume_text)
    canonical_text = "\n".join(str(item.get("text") or "") for item in blocks)
    deterministic_ir = build_resume_ir(
        candidate_id,
        canonical_text or resume_text,
        project_title_line_numbers=_layout_project_title_hints(blocks),
        section_by_line_number=section_by_line(blocks),
    )
    _attach_block_ids(deterministic_ir, blocks)
    validation = validate_resume_structure(deterministic_ir, blocks)
    outline_repaired = not validation.accepted
    traces: list[dict[str, Any]] = []
    final_ir = deterministic_ir
    final_validation = validation
    if outline_repaired:
        payload, traces = repair_resume_structure(
            blocks=blocks,
            deterministic_ir=deterministic_ir,
            validation=validation,
            llm_config=llm_config,
        )
        if payload is None:
            return _structure_failed(
                deterministic_ir, validation, traces, "outline_llm_disabled"
            )
        try:
            final_ir = assemble_repaired_ir(
                candidate_id=candidate_id,
                resume_text=resume_text,
                blocks=blocks,
                payload=payload,
                deterministic_ir=deterministic_ir,
                repair_trace=traces,
            )
            final_validation = validate_resume_structure(final_ir, blocks)
        except Exception as exc:
            return _structure_failed(
                deterministic_ir,
                validation,
                traces,
                f"{type(exc).__name__}:{str(exc)[:240]}",
            )
        if not final_validation.accepted:
            return _structure_failed(
                final_ir,
                final_validation,
                traces,
                "outline_repair_validation_failed",
            )

    work_units, work_unit_traces, work_unit_error = extract_work_units(
        final_ir,
        llm_config=llm_config,
    )
    traces.extend(work_unit_traces)
    if work_units is None:
        return _structure_failed(
            final_ir,
            final_validation,
            traces,
            work_unit_error or "work_unit_extraction_failed",
        )
    final_ir.scorable_work_units = work_units
    skill_statements, skill_traces, skill_error = extract_skill_claims(
        final_ir,
        llm_config=llm_config,
    )
    traces.extend(skill_traces)
    if skill_statements is None:
        return _structure_failed(
            final_ir,
            final_validation,
            traces,
            skill_error or "skill_claim_extraction_failed",
        )
    final_ir.skill_statements = skill_statements
    final_ir.structuring_provenance = {
        **final_ir.structuring_provenance,
        "mode": (
            "llm_outline_repair_and_work_units_v1"
            if outline_repaired
            else "deterministic_outline_llm_work_units_v1"
        ),
        "source_block_count": len(blocks),
        "validation": asdict(final_validation),
        "work_unit_count": len(work_units),
        "skill_claim_count": sum(
            len(item.get("skill_claims") or []) for item in skill_statements
        ),
    }
    return {
        "schema_version": "resume_structure_result_v2",
        "status": "repaired" if outline_repaired else "passed",
        "method": (
            "llm_outline_repair+llm_work_units"
            if outline_repaired
            else "deterministic_outline+llm_work_units"
        ),
        "resume_ir": asdict(final_ir),
        "validation": asdict(final_validation),
        "repair_trace": traces,
    }


def resume_ir_from_structure(
    result: dict[str, Any],
    *,
    candidate_id: str | None = None,
) -> VerifiedResumeIR:
    # 1. 只有通过全部结构化与评分证据校验的结果才能发布为正式 ResumeProfile。
    #    技术失败不能靠“人工确认”绕过：当前页面没有编辑工作单元的能力，放行只会
    #    将缺失证据推迟到初筛阶段再失败。
    result_status = str(result.get("status") or "")
    if result_status not in {"passed", "repaired"}:
        raise ResumeStructureReviewRequired(result)
    # 2. 读取持久化的简历事实 JSON，缺少该对象说明上游结构化结果不完整。
    payload = result.get("resume_ir")
    if not isinstance(payload, dict):
        raise ValueError("resume_structure_missing_resume_ir")
    # 3. 恢复输入质量报告，供初筛入口在评分前再次检查。
    report_payload = payload.get("input_quality_report")
    report = (
        ResumeInputQualityReport(**report_payload)
        if isinstance(report_payload, dict)
        else None
    )
    # 4. 将候选人片段、经历单元、原文证据、工作单元和技能声明恢复为强类型事实对象。
    return VerifiedResumeIR(
        resume_ir_version=str(payload["resume_ir_version"]),
        candidate_id=candidate_id or str(payload["candidate_id"]),
        resume_raw_sha256=str(payload["resume_raw_sha256"]),
        resume_redacted_sha256=str(payload["resume_redacted_sha256"]),
        candidate_spans=[
            CandidateSpan(**item) for item in payload.get("candidate_spans", [])
        ],
        experience_units=[
            ExperienceUnit(**item) for item in payload.get("experience_units", [])
        ],
        candidate_facts=dict(payload.get("candidate_facts") or {}),
        source_bullets=[
            SourceBullet(**item) for item in payload.get("source_bullets", [])
        ],
        scorable_work_units=[
            ScorableWorkUnit(
                work_unit_id=str(item["work_unit_id"]),
                project_id=str(item.get("project_id") or item.get("experience_unit_id")),
                raw_text=str(item.get("raw_text") or ""),
                source_line_start=item.get("source_line_start"),
                source_line_end=item.get("source_line_end"),
                source_block_ids=list(item.get("source_block_ids") or []),
                source_refs=list(item.get("source_refs") or []),
            )
            for item in payload.get("scorable_work_units", [])
        ],
        skill_statements=list(payload.get("skill_statements") or []),
        redaction_warnings=list(payload.get("redaction_warnings", [])),
        project_context_items=[
            ProjectContextItem(**item)
            for item in payload.get("project_context_items", [])
        ],
        input_quality_report=report,
        structuring_provenance=dict(payload.get("structuring_provenance", {})),
    )


def _normalize_blocks(
    blocks: list[dict[str, Any]] | None,
    resume_text: str,
) -> list[dict[str, Any]]:
    valid = [
        dict(item)
        for item in (blocks or [])
        if (
            isinstance(item, dict)
            and str(item.get("text") or "").strip()
            # MinerU may append repeated headers/footers after body blocks. They
            # are page chrome, not resume evidence, and otherwise inherit the
            # last body section and become false WorkUnits.
            and str(item.get("block_type") or "").casefold()
            not in {"header", "footer", "page_header", "page_footer"}
        )
    ]
    if not valid:
        valid = [
            {
                "block_id": f"B_{index:04d}",
                "page": None,
                "order": index,
                "text": line.strip(),
                "block_type": "text",
                "source_parser": "legacy_text",
            }
            for index, line in enumerate(resume_text.splitlines(), start=1)
            if line.strip()
        ]
    else:
        valid = _order_parser_blocks(valid)
        for sequence, item in enumerate(valid, start=1):
            item["order"] = sequence
        expanded: list[dict[str, Any]] = []
        for item in sorted(
            valid,
            key=lambda value: int(value.get("order") or 0),
        ):
            expanded.extend(expand_logical_block(item))
        valid = _merge_soft_wrapped_blocks(expanded)
    seen: set[str] = set()
    for index, item in enumerate(
        sorted(valid, key=lambda value: int(value.get("order") or 0)),
        start=1,
    ):
        block_id = str(item.get("block_id") or f"B_{index:04d}")
        if block_id in seen:
            block_id = f"{block_id}_{index}"
        seen.add(block_id)
        item["block_id"] = block_id
        item["logical_block_id"] = block_id
        item["order"] = index
        item["text"] = str(item.get("text") or "").strip()
    return sorted(valid, key=lambda value: int(value.get("order") or 0))


def _order_parser_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Repair clear parser order inversions while preserving normal reading order.

    MinerU may append a table or reconciliation-only text after later-page
    content.  Page is therefore always the primary key.  Within a page bbox
    ordering is used only after a large upward jump proves the parser order is
    inconsistent; ordinary multi-column pages keep their parser order.
    """
    deduplicated: list[dict[str, Any]] = []
    seen: set[tuple[Any, str, tuple[float, ...] | None]] = set()
    for item in sorted(blocks, key=lambda value: int(value.get("order") or 0)):
        key = (
            item.get("page"),
            _dedupe_text(str(item.get("text") or "")),
            _bbox_key(item.get("bbox")),
        )
        if key[1] and key[2] is not None and key in seen:
            continue
        seen.add(key)
        deduplicated.append(item)

    pages: dict[Any, list[dict[str, Any]]] = {}
    page_order: list[Any] = []
    for item in deduplicated:
        page = item.get("page")
        if page not in pages:
            pages[page] = []
            page_order.append(page)
        pages[page].append(item)
    numeric_pages = sorted(
        (page for page in page_order if isinstance(page, (int, float))),
        key=float,
    )
    ordered_pages = [*numeric_pages, *(page for page in page_order if page not in numeric_pages)]
    output: list[dict[str, Any]] = []
    for page in ordered_pages:
        page_blocks = pages[page]
        positioned = [
            item for item in page_blocks if _bbox_top(item.get("bbox")) is not None
        ]
        tops = [_bbox_top(item.get("bbox")) for item in positioned]
        has_large_inversion = any(
            current is not None and previous is not None and current + 40 < previous
            for previous, current in zip(tops, tops[1:])
        )
        if has_large_inversion and len(positioned) == len(page_blocks):
            page_blocks = sorted(
                page_blocks,
                key=lambda item: (
                    _bbox_top(item.get("bbox")) or 0.0,
                    _bbox_left(item.get("bbox")) or 0.0,
                    int(item.get("order") or 0),
                ),
            )
        output.extend(page_blocks)
    return output


def _dedupe_text(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def _bbox_top(value: Any) -> float | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        return float(value[1])
    except (TypeError, ValueError):
        return None


def _bbox_left(value: Any) -> float | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        return float(value[0])
    except (TypeError, ValueError):
        return None


def _bbox_key(value: Any) -> tuple[float, ...] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        return tuple(round(float(item), 1) for item in value)
    except (TypeError, ValueError):
        return None


def _merge_soft_wrapped_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Join physical PDF line wraps without joining independent logical blocks."""
    merged: list[dict[str, Any]] = []
    for item in blocks:
        if merged and _is_soft_wrap(merged[-1], item):
            previous = merged[-1]
            previous["text"] = (
                str(previous.get("text") or "").rstrip()
                + str(item.get("text") or "").lstrip()
            )
            fragment_ids = list(
                previous.get("source_fragment_ids")
                or [str(previous.get("block_id") or "")]
            )
            fragment_ids.extend(
                item.get("source_fragment_ids")
                or [str(item.get("block_id") or "")]
            )
            previous["source_fragment_ids"] = [
                value for value in fragment_ids if value
            ]
            continue
        merged.append(dict(item))
    return merged


def _is_soft_wrap(previous: dict[str, Any], current: dict[str, Any]) -> bool:
    if previous.get("field_label") or current.get("field_label"):
        return False
    parent_id = previous.get("parent_block_id")
    if not parent_id or parent_id != current.get("parent_block_id"):
        return False
    previous_text = str(previous.get("text") or "").strip()
    current_text = str(current.get("text") or "").strip()
    if len(previous_text) < 32 or not current_text:
        return False
    if previous_text.endswith(("。", "！", "？", "；", ".", "!", "?", ";")):
        return False
    if current_text in {
        "基础信息", "个人信息", "基本信息", "教育背景", "教育经历",
        "专业技能", "技能清单", "项目经历", "工作经历", "实习经历",
        "科研经历", "---", "--", "-",
    }:
        return False
    if re.match(r"^(?:[-•·▪]|https?://|www\.|[\w.+-]+@[\w.-]+)", current_text):
        return False
    if re.match(r"^\d{4}[./年-]\d{1,2}", current_text):
        return False
    return True


def _attach_block_ids(
    resume_ir: VerifiedResumeIR,
    blocks: list[dict[str, Any]],
) -> None:
    by_line = {
        # Logical table rows are derived from one immutable parser Block. Keep
        # public provenance on that parent so correction APIs can resolve it;
        # the exact row text remains in CandidateSpan/SourceBullet.quote.
        index: source_block_id(item)
        for index, item in enumerate(blocks, start=1)
    }
    for span in resume_ir.candidate_spans:
        block_id = by_line.get(span.source_line_start)
        span.source_block_ids = [block_id] if block_id else []
    span_blocks = {
        (
            span.experience_unit_id,
            span.source_line_start,
            span.source_line_end,
            span.text,
        ): span.source_block_ids
        for span in resume_ir.candidate_spans
    }
    for bullet in resume_ir.source_bullets:
        bullet.source_block_ids = list(
            span_blocks.get(
                (
                    bullet.experience_unit_id,
                    bullet.source_line_start,
                    bullet.source_line_end,
                    bullet.raw_text,
                ),
                [],
            )
        )
    for context in resume_ir.project_context_items:
        context.source_block_ids = [
            block_id
            for span in resume_ir.candidate_spans
            if span.source_line_start == context.source_line_start
            for block_id in span.source_block_ids
        ]


def _layout_project_title_hints(
    blocks: list[dict[str, Any]],
) -> set[int]:
    """Return layout-backed candidate boundary lines.

    The structuralizer still uses text rules as the source of truth.  Layout is
    only an additional local signal: a large gap is normalized by the typical
    line height of the same page, and a table-style date row is promoted only
    when the next row starts a project description.  This avoids using a
    document-wide mean, which is unstable across pages and columns.
    """
    sizes = [
        float(item["font_size"])
        for item in blocks
        if isinstance(item.get("font_size"), (int, float))
        and float(item["font_size"]) > 0
    ]
    body_size = median(sizes) if sizes else None
    field_labels = {
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
    action_cues = {
        "负责",
        "主导",
        "参与",
        "实现",
        "开发",
        "构建",
        "设计",
        "完成",
        "优化",
    }
    hints: set[int] = set()
    local_gaps_by_page: dict[Any, list[float]] = {}
    for previous, current in zip(blocks, blocks[1:]):
        if previous.get("page") != current.get("page"):
            continue
        previous_box = previous.get("bbox")
        current_box = current.get("bbox")
        if (
            not isinstance(previous_box, (list, tuple))
            or len(previous_box) != 4
            or not isinstance(current_box, (list, tuple))
            or len(current_box) != 4
        ):
            continue
        try:
            previous_bottom = float(previous_box[3])
            current_top = float(current_box[1])
            gap = current_top - previous_bottom
        except (TypeError, ValueError):
            continue
        if gap >= 0:
            local_gaps_by_page.setdefault(current.get("page"), []).append(gap)
    typical_gap_by_page = {
        page: median(values)
        for page, values in local_gaps_by_page.items()
        if values
    }

    for line_number, item in enumerate(blocks, start=1):
        text = str(item.get("text") or "").strip()
        next_text = (
            str(blocks[line_number].get("text") or "").strip()
            if line_number < len(blocks)
            else ""
        )
        label = text.split("：", 1)[0].split(":", 1)[0].strip()
        if (
            not text
            or len(text) > 60
            or label in field_labels
            or text.endswith(("：", ":"))
            or any(text.startswith(cue) for cue in action_cues)
        ):
            continue
        styled = (
            item.get("block_type") in {"title", "heading"}
            or item.get("heading_level") is not None
            or item.get("is_bold") is True
            or (
                body_size is not None
                and isinstance(item.get("font_size"), (int, float))
                and float(item["font_size"]) >= body_size * 1.15
            )
        )
        if styled:
            hints.add(line_number)
            continue

        # A table row such as ``-- | -- | 2024-05 ~ 至今`` has no project
        # identity of its own.  The following description label makes it a
        # reliable boundary inside an experience section.
        if _is_placeholder_date_row(text) and _starts_with_project_context(next_text):
            hints.add(line_number)
            continue

        # Use only a local, page-level gap signal.  ``typical_gap`` is a
        # fallback when coordinates exist but font metrics are unavailable.
        typical_gap = typical_gap_by_page.get(item.get("page"))
        if typical_gap is not None and _is_layout_gap_boundary(blocks, line_number, typical_gap):
            if _looks_like_boundary_candidate(text, next_text):
                hints.add(line_number)
    return hints


def _is_placeholder_date_row(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    if not re.search(r"(?:19|20)\d{2}", compact):
        return False
    meaningful = re.sub(r"[|｜\-_—–~～﹣－至到今0-9./年月个()（）]", "", compact)
    # Standard-form PDFs do not always preserve table separators.  A row such
    # as ``-- -- 2024-05～至今`` is still a layout boundary when the following
    # row starts with an explicit project-description field.  Requiring the
    # row to contain no semantic words keeps ordinary dated prose out.
    return not meaningful


def _starts_with_project_context(text: str) -> bool:
    compact = re.sub(r"[\s：:]+", "", text)
    return any(
        compact.startswith(prefix)
        for prefix in ("项目描述", "项目简介", "项目介绍", "项目概述", "项目名称")
    )


def _is_layout_gap_boundary(
    blocks: list[dict[str, Any]], line_number: int, typical_gap: float
) -> bool:
    index = line_number - 1
    if index <= 0 or index >= len(blocks):
        return False
    previous = blocks[index - 1]
    current = blocks[index]
    if previous.get("page") != current.get("page"):
        return False
    previous_box = previous.get("bbox")
    current_box = current.get("bbox")
    if not isinstance(previous_box, (list, tuple)) or not isinstance(current_box, (list, tuple)):
        return False
    if len(previous_box) != 4 or len(current_box) != 4:
        return False
    try:
        gap = float(current_box[1]) - float(previous_box[3])
    except (TypeError, ValueError):
        return False
    return gap >= max(typical_gap * 1.8, typical_gap + 4.0)


def _looks_like_boundary_candidate(text: str, next_text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    if not compact or len(compact) > 100:
        return False
    if re.search(r"(?:负责|主导|参与|实现|开发|构建|设计|完成|优化)", text):
        return False
    if _starts_with_project_context(text):
        return False
    return bool(
        re.search(r"(?:项目|系统|平台|课题|实习|工作|研究|研发|公司|集团|研究院)", compact)
        or _is_placeholder_date_row(text)
        or _starts_with_project_context(next_text)
    )


def _structure_failed(
    resume_ir: VerifiedResumeIR,
    validation: StructureValidation,
    traces: list[dict[str, Any]],
    reason: str,
) -> dict[str, Any]:
    return {
        "schema_version": "resume_structure_result_v2",
        # 结构化模型不可用、输出不合法或评分证据不完整均是技术失败，而不是
        # 用户点击“确认”能够解决的业务歧义。由上层 StepRunner 记录失败、重试
        # 与终态，并由页面提供“重新解析”。
        "status": "failed",
        "method": "llm_repair",
        "resume_ir": asdict(resume_ir),
        "validation": asdict(validation),
        "repair_trace": traces,
        "failure_reason": reason,
    }
