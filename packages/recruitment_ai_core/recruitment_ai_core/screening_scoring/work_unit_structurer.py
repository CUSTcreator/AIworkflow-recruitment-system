from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .contracts import SourceBullet, VerifiedResumeIR


def build_resume_profile(resume_ir: VerifiedResumeIR) -> dict[str, Any]:
    """Publish only the facts consumed by routing, screening and interviews.

    SourceBullet and the pre-flattened collections are execution-time helpers. They
    are deliberately not duplicated into the published profile.
    """
    bullets_by_id = {
        item.source_bullet_id: item
        for item in resume_ir.source_bullets
    }
    return {
        "resume_profile_version": "resume_profile_v1_2",
        "resume_profile_version_id": f"RP_v1_2_{resume_ir.candidate_id}_{resume_ir.resume_redacted_sha256[:12]}",
        "candidate_id": resume_ir.candidate_id,
        "resume_raw_sha256": resume_ir.resume_raw_sha256,
        "resume_redacted_sha256": resume_ir.resume_redacted_sha256,
        "candidate_facts": dict(resume_ir.candidate_facts or {}),
        "skill_claims": _skill_claims(resume_ir.skill_statements),
        "experience_units": [
            {
                "experience_unit_id": unit.experience_unit_id,
                "title": unit.title,
                "context_items": [
                    asdict(item)
                    for item in resume_ir.project_context_items
                    if item.experience_unit_id == unit.experience_unit_id
                ],
                "work_units": [
                    _work_unit_payload(work_unit, bullets_by_id)
                    for work_unit in resume_ir.scorable_work_units
                    if work_unit.project_id == unit.experience_unit_id
                ],
            }
            for unit in resume_ir.experience_units
        ],
        "input_quality_report": (
            asdict(resume_ir.input_quality_report)
            if resume_ir.input_quality_report is not None
            else None
        ),
        "structuring_provenance": dict(resume_ir.structuring_provenance or {}),
    }


def _skill_claims(statements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            **claim,
            "skill_statement_id": statement["skill_statement_id"],
        }
        for statement in statements
        if isinstance(statement, dict)
        for claim in list(statement.get("skill_claims") or [])
        if isinstance(claim, dict)
    ]


def _work_unit_payload(
    work_unit: Any,
    bullets_by_id: dict[str, SourceBullet],
) -> dict[str, Any]:
    refs = [dict(item) for item in list(work_unit.source_refs or [])]
    linked = [
        bullets_by_id[str(item.get("bullet_id") or "")]
        for item in refs
        if str(item.get("bullet_id") or "") in bullets_by_id
    ]
    raw_text = str(work_unit.raw_text or "").strip() or "；".join(
        str(item.get("quote") or "").strip()
        for item in refs
        if str(item.get("quote") or "").strip()
    )
    return {
        "work_unit_id": work_unit.work_unit_id,
        "project_id": work_unit.project_id,
        "raw_text": raw_text,
        "source_line_start": work_unit.source_line_start or (
            min((item.source_line_start for item in linked), default=None)
        ),
        "source_line_end": work_unit.source_line_end or (
            max((item.source_line_end for item in linked), default=None)
        ),
        "source_block_ids": list(work_unit.source_block_ids or []) or [
            block_id
            for item in linked
            for block_id in item.source_block_ids
        ],
        "source_refs": refs,
    }
