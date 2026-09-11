"""简历结构化的活动级纯函数。

本模块不访问数据库或 Workflow。规则层只冻结项目边界和逐字原文，WorkUnit 与
SkillClaim 的语义整理分别由独立 LLM Activity 完成，最终结果仍由后端回填原文。
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from recruitment_ai_core.screening_scoring.contracts import (
    CandidateSpan,
    ExperienceUnit,
    ProjectContextItem,
    ScorableWorkUnit,
    SourceBullet,
)
from recruitment_ai_core.llm.errors import LLMResponseError
from recruitment_ai_core.screening_scoring.resume_structurer import build_resume_ir
from recruitment_ai_core.screening_scoring.resume_structurer import _is_explicit_duty_text
from recruitment_ai_core.screening_scoring.section_resolver import section_by_line

from .assembler import assemble_repaired_ir
from .llm_repair import repair_resume_structure
from .pipeline import (
    _attach_block_ids,
    _layout_project_title_hints,
    _normalize_blocks,
    resume_ir_from_structure,
)
from .skill_claim_extractor import (
    build_degraded_skill_statements,
    extract_skill_claims,
)
from .validator import (
    StructureValidation,
    validate_resume_evidence_coverage,
    validate_resume_structure,
)
from .work_unit_extractor import (
    _bind_work_units_to_bullets,
    _extract_project,
    _project_input,
    build_degraded_project_work_units,
)


def prepare_structure(*, candidate_id: str, resume_text: str, document_blocks: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Build a deterministic resume skeleton and freeze its input blocks."""
    blocks = _normalize_blocks(document_blocks, resume_text)
    canonical_text = "\n".join(str(item.get("text") or "") for item in blocks)
    resume_ir = build_resume_ir(
        candidate_id,
        canonical_text or resume_text,
        project_title_line_numbers=_layout_project_title_hints(blocks),
        section_by_line_number=section_by_line(blocks),
    )
    _attach_block_ids(resume_ir, blocks)
    validation = validate_resume_structure(resume_ir, blocks)
    return {
        "candidateId": candidate_id,
        "resumeText": resume_text,
        "blocks": blocks,
        "resumeIr": asdict(resume_ir),
        "validation": asdict(validation),
        "outlineRepaired": False,
        "traces": [],
    }


def repair_outline(prepared: dict[str, Any]) -> dict[str, Any]:
    """Use the outline repair only when the deterministic skeleton is invalid."""
    validation = StructureValidation(**dict(prepared["validation"]))
    if validation.accepted:
        return prepared
    blocks = list(prepared["blocks"])
    deterministic = _restore_ir(prepared["resumeIr"])
    payload, traces = repair_resume_structure(
        blocks=blocks, deterministic_ir=deterministic, validation=validation, llm_config=None
    )
    if payload is None:
        raise LLMResponseError("resume_outline_repair_empty")
    try:
        repaired = assemble_repaired_ir(
            candidate_id=str(prepared["candidateId"]), resume_text=str(prepared["resumeText"]),
            blocks=blocks, payload=payload, deterministic_ir=deterministic, repair_trace=traces,
        )
    except ValueError as exc:
        raise LLMResponseError(f"resume_outline_repair_invalid:{exc}") from exc
    final_validation = validate_resume_structure(repaired, blocks)
    if not final_validation.accepted:
        raise LLMResponseError("resume_outline_repair_validation_failed")
    return {
        **prepared,
        "resumeIr": asdict(repaired),
        "validation": asdict(final_validation),
        "outlineRepaired": True,
        "traces": [*list(prepared.get("traces") or []), *traces],
    }


def work_unit_inputs(prepared: dict[str, Any]) -> list[dict[str, Any]]:
    """为有候选工作事实的项目生成输入；纯背景项目合法地保留空WorkUnit。"""
    resume_ir = _restore_ir(prepared["resumeIr"])
    return [
        _project_input(resume_ir, unit.experience_unit_id, unit.title)
        for unit in resume_ir.experience_units
        if unit.source_bullet_ids
    ]


def extract_project_work_units(project: dict[str, Any]) -> dict[str, Any]:
    """调用一次项目级模型，只返回Bullet分组，逐字证据由后端回填。"""
    units, traces = _extract_project(project, None)
    return {
        "projectId": project["project_id"],
        "workUnits": [asdict(item) for item in units],
        "traces": traces,
    }


def degrade_project_work_units(
    project: dict[str, Any], error: Exception
) -> dict[str, Any]:
    """Return source-only WorkUnits when one project's semantic grouping fails."""
    units = build_degraded_project_work_units(project)
    return {
        "projectId": project["project_id"],
        "workUnits": [asdict(item) for item in units],
        "traces": [{
            "outcome": "degraded",
            "resolution_code": "work_unit_source_bullets_preserved",
            "project_id": project["project_id"],
            "error_type": type(error).__name__,
            "preserved_work_unit_count": len(units),
        }],
    }


def apply_work_units(
    prepared: dict[str, Any], results: list[dict[str, Any]]
) -> dict[str, Any]:
    """汇总各项目标准结果；本函数不得发起模型调用或生成替代证据。"""
    resume_ir = _restore_ir(prepared["resumeIr"])
    work_units = [
        ScorableWorkUnit(**unit)
        for result in results
        for unit in list(result.get("workUnits") or [])
    ]
    _bind_work_units_to_bullets(resume_ir, work_units)
    resume_ir.scorable_work_units = work_units
    return {
        **prepared,
        "resumeIr": asdict(resume_ir),
        "traces": [
            *list(prepared.get("traces") or []),
            *[
                trace
                for result in results
                for trace in list(result.get("traces") or [])
            ],
        ],
    }


def extract_skills(prepared: dict[str, Any]) -> dict[str, Any]:
    """整份简历的技能声明只执行一个可恢复的批量 LLM Activity。"""
    statements, traces, error = extract_skill_claims(_restore_ir(prepared["resumeIr"]), llm_config=None)
    if statements is None:
        raise LLMResponseError(error or "resume_skill_claim_extraction_failed")
    return {"skillStatements": statements, "traces": traces}


def degrade_skills(prepared: dict[str, Any], error: Exception) -> dict[str, Any]:
    """Return source-backed skill statements when the skill LLM is exhausted."""
    statements = build_degraded_skill_statements(_restore_ir(prepared["resumeIr"]))
    return {
        "skillStatements": statements,
        "traces": [{
            "outcome": "degraded",
            "resolution_code": "skill_claim_source_text_preserved",
            "error_type": type(error).__name__,
            "preserved_statement_count": len(statements),
        }],
    }


def build_structure_result(
    prepared: dict[str, Any],
    skill_result: dict[str, Any],
    *,
    candidate_facts: dict[str, Any] | None = None,
    manual_correction: dict[str, Any] | None = None,
    replace_with_manual_correction: bool = False,
) -> dict[str, Any]:
    """Assemble the sole publishable structure contract."""
    resume_ir = _restore_ir(prepared["resumeIr"])
    resume_ir.skill_statements = list(skill_result.get("skillStatements") or [])
    resume_ir.candidate_facts = dict(candidate_facts or {})
    if manual_correction:
        _apply_manual_correction(
            resume_ir,
            manual_correction,
            list(prepared["blocks"]),
            replace_existing=replace_with_manual_correction,
        )
    coverage_trace = _ensure_explicit_duty_work_unit_coverage(resume_ir)
    coverage = validate_resume_evidence_coverage(resume_ir)
    if not coverage.accepted:
        raise LLMResponseError(
            "resume_evidence_coverage_invalid:" + ",".join(coverage.errors)
        )
    resume_ir.structuring_provenance = {
        **dict(resume_ir.structuring_provenance or {}),
        "mode": "activity_recoverable_resume_structure_v2",
        "source_block_count": len(prepared["blocks"]),
        "validation": dict(prepared["validation"]),
        "work_unit_count": len(resume_ir.scorable_work_units),
        "skill_claim_count": sum(len(item.get("skill_claims") or []) for item in resume_ir.skill_statements),
        "activity_quality": dict(prepared.get("activityQuality") or {}),
        "evidence_coverage": {
            "accepted": coverage.accepted,
            "warnings": coverage.warnings,
        },
    }
    return {
        "schema_version": "resume_structure_result_v3",
        "status": "repaired" if prepared.get("outlineRepaired") else "passed",
        "method": "manual_correction" if replace_with_manual_correction else "activity_recoverable_structure",
        "resume_ir": asdict(resume_ir),
        "validation": dict(prepared["validation"]),
        "repair_trace": [
            *list(prepared.get("traces") or []),
            *list(skill_result.get("traces") or []),
            *coverage_trace,
        ],
    }


def _ensure_explicit_duty_work_unit_coverage(
    resume_ir: Any,
) -> list[dict[str, Any]]:
    """Fill only missing explicit duties with verbatim one-bullet WorkUnits."""
    referenced = {
        str(ref.get("bullet_id") or "")
        for unit in resume_ir.scorable_work_units
        for ref in unit.source_refs
    }
    missing = [
        bullet
        for bullet in resume_ir.source_bullets
        if _is_explicit_duty_text(bullet.raw_text)
        and bullet.source_bullet_id not in referenced
    ]
    if not missing:
        return []
    counters: dict[str, int] = {}
    for bullet in missing:
        project_id = bullet.experience_unit_id
        counters[project_id] = counters.get(project_id, 0) + 1
        resume_ir.scorable_work_units.append(
            ScorableWorkUnit(
                work_unit_id=f"{project_id}_WU_SOURCE_{counters[project_id]:03d}",
                project_id=project_id,
                raw_text=bullet.raw_text,
                source_line_start=bullet.source_line_start,
                source_line_end=bullet.source_line_end,
                source_block_ids=list(bullet.source_block_ids),
                source_refs=[{
                    "bullet_id": bullet.source_bullet_id,
                    "quote": bullet.raw_text,
                }],
            )
        )
    _bind_work_units_to_bullets(resume_ir, resume_ir.scorable_work_units)
    return [{
        "outcome": "degraded",
        "resolution_code": "explicit_duty_source_work_unit_preserved",
        "source_bullet_ids": [item.source_bullet_id for item in missing],
    }]


def build_manual_correction_structure(
    *,
    candidate_id: str,
    resume_text: str,
    document_blocks: list[dict[str, Any]],
    manual_correction: dict[str, Any],
    candidate_facts: dict[str, Any],
) -> dict[str, Any]:
    """Build a publishable structure from immutable blocks and user-confirmed choices.

    This deliberately does not call the outline or skill LLM. A manual correction
    is the recovery path when those calls are unavailable or their result is not
    reliable enough to publish.
    """
    prepared = prepare_structure(
        candidate_id=candidate_id,
        resume_text=resume_text,
        document_blocks=document_blocks,
    )
    validation = dict(prepared.get("validation") or {})
    validation["accepted"] = True
    validation["warnings"] = [
        *list(validation.get("warnings") or []),
        "manual_correction_confirmed_source_blocks",
    ]
    prepared = {
        **prepared,
        "validation": validation,
        "outlineRepaired": True,
        "activityQuality": {
            "manualCorrection": {
                "outcomeKinds": {"manual_correction": "user_confirmed"},
                "summaries": {
                    "manual_correction": {
                        "usable": True,
                        "source": "immutable_blocks",
                        "llm_bypassed": True,
                    }
                },
            }
        },
        "traces": [{
            "workflow": "resume_manual_correction",
            "outcome": "completed",
            "resolution_code": "manual_correction_llm_bypassed",
        }],
    }
    return build_structure_result(
        prepared,
        {"skillStatements": [], "traces": []},
        candidate_facts=candidate_facts,
        manual_correction=manual_correction,
        replace_with_manual_correction=True,
    )


def _apply_manual_correction(
    resume_ir,
    correction: dict[str, Any],
    blocks: list[dict[str, Any]],
    *,
    replace_existing: bool = False,
) -> None:
    """Apply a validated correction without accepting arbitrary evidence text."""
    block_by_id = {
        str(item.get("block_id") or ""): item
        for item in blocks
        if str(item.get("block_id") or "") and str(item.get("text") or "").strip()
    }
    corrected_facts = correction.get("candidate_facts")
    if isinstance(corrected_facts, dict):
        # The correction UI edits education and experience years.  Preserve
        # source-derived qualification facts that the user did not replace;
        # otherwise correcting an unrelated field would silently break hard
        # screening for certificates and language levels.
        resume_ir.candidate_facts = {
            **dict(resume_ir.candidate_facts or {}),
            **dict(corrected_facts),
        }

    if replace_existing:
        resume_ir.experience_units = []
        resume_ir.project_context_items = []
        resume_ir.source_bullets = []
        resume_ir.scorable_work_units = []
        resume_ir.skill_statements = []

    for index, item in enumerate(list(correction.get("experience_units") or []), start=1):
        if not isinstance(item, dict):
            continue
        unit_id = str(item.get("experience_unit_id") or "").strip() or f"MANUAL_EXP_{index:03d}"
        unit = next((value for value in resume_ir.experience_units if value.experience_unit_id == unit_id), None)
        if unit is None:
            unit = ExperienceUnit(unit_id, str(item.get("title") or "项目经历").strip() or "项目经历", [])
            resume_ir.experience_units.append(unit)
        elif str(item.get("title") or "").strip():
            unit.title = str(item["title"]).strip()

        context_refs = [ref for ref in list(item.get("context_source_refs") or []) if isinstance(ref, dict)]
        if context_refs:
            resume_ir.project_context_items = [
                value for value in resume_ir.project_context_items
                if value.experience_unit_id != unit_id
            ]
            for context_index, ref in enumerate(context_refs, start=1):
                block = block_by_id.get(str(ref.get("block_id") or ""))
                if block is None:
                    continue
                resume_ir.project_context_items.append(ProjectContextItem(
                    context_id=f"{unit_id}_MANUAL_CTX_{context_index:03d}",
                    experience_unit_id=unit_id,
                    context_type="other_context",
                    text=str(block.get("text") or "").strip(),
                    source_line_start=int(block.get("source_line_start") or block.get("line_start") or 0),
                    source_line_end=int(block.get("source_line_end") or block.get("line_end") or 0),
                    source_block_ids=[str(ref.get("block_id"))],
                ))

        work_refs = [ref for ref in list(item.get("work_source_refs") or []) if isinstance(ref, dict)]
        if work_refs:
            old_bullet_ids = {
                value.source_bullet_id for value in resume_ir.source_bullets
                if value.experience_unit_id == unit_id
            }
            resume_ir.source_bullets = [
                value for value in resume_ir.source_bullets
                if value.experience_unit_id != unit_id
            ]
            resume_ir.scorable_work_units = [
                value for value in resume_ir.scorable_work_units
                if value.project_id != unit_id
            ]
            unit.source_bullet_ids = []
            for work_index, ref in enumerate(work_refs, start=1):
                block = block_by_id.get(str(ref.get("block_id") or ""))
                if block is None:
                    continue
                text = str(block.get("text") or "").strip()
                bullet_id = f"{unit_id}_MANUAL_SB_{work_index:03d}"
                work_unit_id = f"{unit_id}_MANUAL_WU_{work_index:03d}"
                bullet = SourceBullet(
                    source_bullet_id=bullet_id,
                    experience_unit_id=unit_id,
                    raw_text=text,
                    source_line_start=int(block.get("source_line_start") or block.get("line_start") or 0),
                    source_line_end=int(block.get("source_line_end") or block.get("line_end") or 0),
                    work_unit_ids=[work_unit_id],
                    source_block_ids=[str(ref.get("block_id"))],
                )
                resume_ir.source_bullets.append(bullet)
                unit.source_bullet_ids.append(bullet_id)
                resume_ir.scorable_work_units.append(ScorableWorkUnit(
                    work_unit_id=work_unit_id,
                    project_id=unit_id,
                    raw_text=text,
                    source_line_start=bullet.source_line_start,
                    source_line_end=bullet.source_line_end,
                    source_block_ids=list(bullet.source_block_ids),
                    source_refs=[{"bullet_id": bullet_id, "quote": text}],
                ))

    claims = [item for item in list(correction.get("skill_claims") or []) if isinstance(item, dict)]
    if claims or replace_existing:
        first_refs = list(claims[0].get("source_refs") or []) if claims else []
        resume_ir.skill_statements = ([{
            "skill_statement_id": "MANUAL_SKILL_CLAIMS",
            "source_ref": dict(first_refs[0]) if first_refs and isinstance(first_refs[0], dict) else {},
            "skill_claims": claims,
        }] if claims else [])


def _restore_ir(payload: dict[str, Any]):
    return resume_ir_from_structure({"status": "passed", "resume_ir": payload})
