from __future__ import annotations

import pytest

from recruitment_ai_core.common.interview_parse_validation import (
    build_allowed_targets,
    validate_result_consistency,
    validate_segments,
)


def _record(*, record_type: str = "free_note") -> dict:
    return {
        "record_id": "IR_1",
        "record_type": record_type,
        "question_id": "Q1" if record_type == "question_answer" else None,
        "segments": [{"segment_id": "SRC_1", "text": "补充项目使用缓存"}],
    }


def _snapshot() -> dict:
    return {
        "work_unit_versions": [
            {"work_unit_id": "WU_1", "status": "active"},
            {"work_unit_id": "WU_2", "status": "active"},
        ],
        "skill_claim_states": [{"skill_claim_id": "SC_1", "effective": True}],
    }


def _job_profile() -> dict:
    return {
        "job_capabilities": [
            {"job_capability_id": "JC_1"},
            {"job_capability_id": "JC_2"},
        ],
    }


def test_free_note_uses_complete_category_target_set() -> None:
    allowed = build_allowed_targets(
        record=_record(), snapshot=_snapshot(), job_profile=_job_profile(),
        question_bindings={},
    )
    assert allowed["experience_update"] == [
        {"target_type": "work_unit", "target_id": "WU_1"},
        {"target_type": "work_unit", "target_id": "WU_2"},
    ]
    assert allowed["skill_claim_correction"] == [
        {"target_type": "skill_claim", "target_id": "SC_1"},
    ]
    assert {item["target_id"] for item in allowed["interview_observation"]} >= {
        "JC_1", "JC_2",
    }


def test_incomplete_segment_target_matrix_is_rejected() -> None:
    record = _record()
    allowed = build_allowed_targets(
        record=record, snapshot=_snapshot(), job_profile=_job_profile(),
        question_bindings={},
    )
    payload = {
        "segments": [{
            "segment_id": "SEG_1",
            "category": "experience_update",
            "raw_text": "补充项目使用缓存",
            "source_refs": [{"segment_id": "SRC_1", "quote": "补充项目使用缓存"}],
            "resolution_status": "no_effect",
            "review_reason": None,
            "target_decisions": [{
                "target_type": "work_unit", "target_id": "WU_1", "selected": False,
            }],
        }],
    }
    with pytest.raises(ValueError, match="target_decision_matrix_mismatch"):
        validate_segments(payload, record=record, allowed_targets=allowed)


def test_selected_target_must_have_matching_derived_result() -> None:
    segments = {
        "SEG_1": {
            "segment_id": "SEG_1",
            "category": "interview_observation",
            "resolution_status": "resolved",
            "target_decisions": [{
                "target_type": "job_capability",
                "target_id": "JC_1",
                "selected": True,
            }],
        },
    }
    result = {
        "experience_updates": [],
        "skill_claim_corrections": [],
        "interview_observations": [{
            "source_refs": [{"segment_id": "SEG_1", "quote": "现场表现"}],
            "target_job_capability_ids": ["JC_2"],
            "target_preset_indicator_ids": [],
        }],
        "non_scoring": [],
    }
    with pytest.raises(ValueError, match="selected_result_mismatch"):
        validate_result_consistency(segments=segments, result=result)


def test_needs_review_cannot_select_or_generate_scoring_target() -> None:
    record = _record()
    allowed = build_allowed_targets(
        record=record, snapshot=_snapshot(), job_profile=_job_profile(),
        question_bindings={},
    )
    decisions = [
        {**target, "selected": target["target_id"] == "JC_1"}
        for target in allowed["interview_observation"]
    ]
    payload = {
        "segments": [{
            "segment_id": "SEG_1",
            "category": "interview_observation",
            "raw_text": "补充项目使用缓存",
            "source_refs": [{"segment_id": "SRC_1", "quote": "补充项目使用缓存"}],
            "resolution_status": "needs_review",
            "review_reason": "无法判断是否为现场表现",
            "target_decisions": decisions,
        }],
    }
    with pytest.raises(ValueError, match="inactive_segment_has_selected_target"):
        validate_segments(payload, record=record, allowed_targets=allowed)
