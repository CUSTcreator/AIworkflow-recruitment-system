from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from recruitment_ai_core.llm import call_json_llm
from recruitment_ai_core.screening_scoring.contracts import VerifiedResumeIR

from .validator import StructureValidation


WORKFLOW_NAME = "resume_structure_repair"
SCHEMA_NAME = "resume_structure_repair_v1"


def repair_resume_structure(
    *,
    blocks: list[dict[str, Any]],
    deterministic_ir: VerifiedResumeIR,
    validation: StructureValidation,
    llm_config: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    selected = _selected_blocks(blocks, deterministic_ir, validation)
    messages = [
        {
            "role": "system",
            "content": _prompt_text(),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "repair_scope": validation.repair_scope,
                    "source_blocks": selected,
                    "rule_draft": _rule_draft(
                        deterministic_ir,
                        {str(item.get("block_id") or "") for item in selected},
                    ),
                    "validation_errors": validation.errors,
                },
                ensure_ascii=False,
            ),
        },
    ]
    payload, trace = call_json_llm(
        workflow_name=WORKFLOW_NAME,
        messages=messages,
        schema_name=SCHEMA_NAME,
        settings_overrides=llm_config,
        json_schema=_schema(),
    )
    if payload is not None:
        payload["_repair_scope_block_ids"] = [
            str(item.get("block_id") or "") for item in selected
        ]
    return payload, [trace]


def _selected_blocks(
    blocks: list[dict[str, Any]],
    deterministic_ir: VerifiedResumeIR,
    validation: StructureValidation,
) -> list[dict[str, Any]]:
    if validation.repair_scope == "whole_resume_outline":
        selected = blocks
    else:
        experience_ids = {
            block_id
            for span in deterministic_ir.candidate_spans
            if span.section == "experience"
            for block_id in span.source_block_ids
        }
        selected = [
            block for block in blocks if str(block.get("block_id") or "") in experience_ids
        ]
        if not selected:
            selected = blocks
    return [
        {
            "block_id": item.get("block_id"),
            "order": item.get("order"),
            "text": item.get("text"),
        }
        for item in selected
    ]


def _rule_draft(
    resume_ir: VerifiedResumeIR,
    selected_block_ids: set[str],
) -> dict[str, Any]:
    """Return only in-scope block-ID hints in the same shape as the repair output."""
    def selected(ids: list[str]) -> list[str]:
        return [item for item in ids if item in selected_block_ids]

    title_spans = [
        item
        for item in resume_ir.candidate_spans
        if item.section == "experience" and item.rough_type == "experience_title"
    ]

    projects: list[dict[str, list[str]]] = []
    for unit in resume_ir.experience_units:
        content_ids = selected([
            block_id
            for span in resume_ir.candidate_spans
            if span.experience_unit_id == unit.experience_unit_id
            for block_id in span.source_block_ids
        ])
        if not content_ids:
            continue
        first_content_line = min(
            span.source_line_start
            for span in resume_ir.candidate_spans
            if span.experience_unit_id == unit.experience_unit_id
        )
        titles = [
            span
            for span in title_spans
            if span.source_line_start < first_content_line
            and all(block_id in selected_block_ids for block_id in span.source_block_ids)
        ]
        if not titles:
            continue
        projects.append({
            "title_block_ids": list(titles[-1].source_block_ids),
            "content_block_ids": content_ids,
        })

    return {
        "sections": [
            {
                "section_type": section,
                "block_ids": selected([
                    block_id
                    for span in resume_ir.candidate_spans
                    if span.section == section
                    for block_id in span.source_block_ids
                ]),
            }
            for section in sorted({item.section for item in resume_ir.candidate_spans})
            if selected([
                block_id
                for span in resume_ir.candidate_spans
                if span.section == section
                for block_id in span.source_block_ids
            ])
        ],
        "projects": projects,
    }


def _prompt_text() -> str:
    return (
        Path(__file__).with_name("prompts").joinpath("structure_repair.md")
        .read_text(encoding="utf-8")
    )


def _schema() -> dict[str, Any]:
    return json.loads(
        Path(__file__).with_name("schemas").joinpath("structure_repair.json")
        .read_text(encoding="utf-8")
    )
