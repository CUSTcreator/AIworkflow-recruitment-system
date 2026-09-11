from __future__ import annotations

from typing import Any

from .contracts import PostSecondScoringInput


def extract_second_evidence_units(
    input_data: PostSecondScoringInput,
) -> list[dict[str, Any]]:
    """Preserve HR records as traceable segments; the shared LLM parser classifies them."""
    units: list[dict[str, Any]] = []
    records = [
        *((item, "hr_free_note", "hr") for item in input_data.hr_raw_notes),
        *((item, "leader_free_note", "leader") for item in input_data.leader_raw_notes),
    ]
    for record, source_type, owner in records:
        text = str(record.get("content") or "").strip()
        if text:
            units.append(
                _unit(
                    input_data.application_id,
                    len(units) + 1,
                    text,
                    source_type,
                    owner,
                    record.get("rawNotesId"),
                )
            )
    return units


def _unit(
    application_id: str,
    index: int,
    text: str,
    source_type: str,
    owner: str,
    record_id: Any,
) -> dict[str, Any]:
    return {
        "evidence_unit_id": f"IEU_SECOND_{application_id}_{index:03d}",
        "source_type": source_type,
        "source_question_id": None,
        "source_response_id": None,
        "source_interview_record_id": record_id,
        "source_interview_round_id": f"INT_{application_id}_SECOND",
        "source_note_id": record_id,
        "answer_status": "answered",
        "source_draft_item_id": None,
        "owner": owner,
        "raw_text": text,
        "speaker": "interviewer",
        "evidence_summary": text[:240],
        "mapping_status": "unmapped",
        "objective_types": ["interview_record"],
        "focus_area_ids": [],
        "verification_target_ids": [],
        "risk_update_ids": [],
        "first_interview_evidence_unit_ids": [],
        "matched_expected_evidence": [],
        "observed_negative_signals": [],
        "new_evidence": False,
        "contradicts_prior_evidence": False,
        "polarity": "neutral",
        "verification_result": "unclear",
        "confidence": "low",
        "rationale": "二面原始记录，等待共享解析器分类。",
    }
