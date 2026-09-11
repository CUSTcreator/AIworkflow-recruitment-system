from __future__ import annotations

import json
from pathlib import Path

from recruitment_ai_core.decision_summary.contracts import PRESENTATION_BUNDLE_JSON_SCHEMA
from recruitment_ai_core.decision_summary.validator import validate_presentation_bundle
import recruitment_ai_core.decision_summary.llm_adapter as presentation_llm_adapter
import pytest

from recruitment_ai_core.llm.schema_validator import (
    JSONSchemaValidationError,
    validate_json_schema,
)
from recruitment_ai_core.screening_scoring.resume_experience.indicator_evaluator import (
    _evaluation_response,
    _normalize_project_pao,
    _normalize_project_results,
    _normalize_unit_results,
)
from recruitment_ai_core.screening_scoring.resume_experience.llm_gateway import (
    _schema_for_request,
    _transport_schema,
)


def _project_schema() -> dict:
    path = (
        Path(__file__).parents[1]
        / "recruitment_ai_core"
        / "screening_scoring"
        / "resume_experience"
        / "schemas"
        / "project_indicator.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def test_project_pao_schema_allows_null_and_normalizer_drops_only_bad_part() -> None:
    schema = _project_schema()
    valid = {
        "project_id": "P1",
        "project_pao": {
            "problem": None,
            "approach": {
                "summary": "defined approach",
                "work_unit_ids": ["WU1"],
                "evidence_work_unit_ids": [],
            },
            "outcome": None,
        },
        "activated_indicators": [],
    }
    validate_json_schema(valid, schema)

    invalid = json.loads(json.dumps(valid))
    invalid["project_pao"]["problem"] = {
        "summary": "defined problem",
        "work_unit_ids": ["WU1"],
        "evidence_work_unit_ids": [],
    }
    invalid["project_pao"]["approach"] = {"summary": ""}
    # 发给模型的完整 Schema 会拒绝坏字段，本地传输 Schema 则允许业务层逐项降级。
    with pytest.raises(JSONSchemaValidationError):
        validate_json_schema(invalid, schema)
    validate_json_schema(invalid, _transport_schema("project_indicator"))
    normalized, errors = _normalize_project_pao(
        invalid["project_pao"], "P1", [{"work_unit_id": "WU1", "project_id": "P1"}]
    )
    assert normalized["problem"]["summary"] == "defined problem"
    assert normalized["approach"] is None
    assert errors == ["project_pao:approach:invalid_reference"]

    invalid["project_pao"]["approach"] = "wrong type"
    with pytest.raises(JSONSchemaValidationError):
        validate_json_schema(invalid, schema)
    validate_json_schema(invalid, _transport_schema("project_indicator"))


def test_presentation_validator_keeps_reordered_items_addressable() -> None:
    bundle_input = {
        "score_summary": {"total": 77.15, "job_fit": 74.84, "experience": 74.08, "education": 92.0},
        "strengths": [
            {"input_index": 1, "target_name": "A", "target_definition": "", "result_summary": "a"},
            {"input_index": 2, "target_name": "B", "target_definition": "", "result_summary": "b"},
        ],
        "weaknesses": [],
        "verification_focus": [],
    }
    payload = {
        "summary": {
            "strength_sentence": "项目经验能够支撑岗位要求。",
            "risk_sentence": "暂无明确风险。",
            "action_sentence": "面试确认职责边界。",
        },
        "strengths": [
            {"input_index": 1, "title": "A", "summary": "a"},
            {"input_index": 2, "title": "B", "summary": "b"},
        ],
        "weaknesses": [],
        "verification_focus": [],
    }
    validate_json_schema(payload, PRESENTATION_BUNDLE_JSON_SCHEMA)
    assert validate_presentation_bundle(payload, bundle_input)["summary"]["strength_sentence"] == "项目经验能够支撑岗位要求。"

    reordered = {**payload, "strengths": list(reversed(payload["strengths"]))}
    normalized = validate_presentation_bundle(reordered, bundle_input)
    assert [item["input_index"] for item in normalized["strengths"]] == [2, 1]
    partially_bad = {**payload, "strengths": [{"input_index": 1, "title": "", "summary": "bad"}, payload["strengths"][1]]}
    normalized = validate_presentation_bundle(partially_bad, bundle_input)
    assert [item["input_index"] for item in normalized["strengths"]] == [2]


def test_presentation_validator_never_returns_manual_review() -> None:
    bundle_input = {
        "score_summary": {"total": 60.0, "job_fit": 60.0, "experience": 60.0, "education": 60.0},
        "strengths": [], "weaknesses": [], "verification_focus": [],
    }
    normalized = validate_presentation_bundle(
        {"summary": {"strength_sentence": "", "risk_sentence": "", "action_sentence": ""}, "strengths": [], "weaknesses": [], "verification_focus": []},
        bundle_input,
    )
    assert normalized["summary"] == {"strength_sentence": "", "risk_sentence": "", "action_sentence": ""}


def test_presentation_request_does_not_send_retry_error(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_call_json_llm(**kwargs):
        captured.update(kwargs)
        return None, {}

    monkeypatch.setattr(presentation_llm_adapter, "call_json_llm", fake_call_json_llm)
    presentation_llm_adapter.try_generate_presentation_bundle(
        {"score_summary": {"total": 70, "job_fit": 70, "experience": 70, "education": 70}, "strengths": [], "weaknesses": [], "verification_focus": []},
        stage="screening",
        settings_overrides=None,
        validation_error="internal_provider_timeout",
    )

    messages = captured["messages"]
    assert isinstance(messages, list)
    assert "internal_provider_timeout" not in messages[1]["content"]
    assert "previous_validation_error" not in messages[1]["content"]
    assert captured["settings_overrides"]["workflows"]["screening_scoring"]["strict_json_schema"] is False
    assert captured["local_validation_schema"] == {"type": "object"}


def test_work_unit_normalizer_keeps_valid_units_when_one_item_is_bad() -> None:
    results, errors = _normalize_unit_results(
        [
            {"target_id": "WU_OK", "activated_indicators": [{"indicator_id": "I1", "level": 3, "reason": "supported"}]},
            {"target_id": "WU_BAD", "activated_indicators": ["bad item"]},
        ],
        [
            {"work_unit_id": "WU_OK", "project_id": "P1"},
            {"work_unit_id": "WU_BAD", "project_id": "P1"},
        ],
        {"I1"},
    )
    assert [item["work_unit_id"] for item in results] == ["WU_OK"]
    assert errors == ["work_unit_evaluation:WU_BAD:invalid_item"]


def test_indicator_normalizer_accepts_unambiguous_levels_and_classifies_errors() -> None:
    results, errors = _normalize_unit_results(
        [{
            "target_id": "WU_1",
            "activated_indicators": [
                {"indicator_id": "I1", "level": "3", "reason": " supported "},
                {"indicator_id": "I2", "level": 0, "reason": "inactive placeholder"},
                {"indicator_id": "UNKNOWN", "level": 2, "reason": "bad id"},
                {"indicator_id": "I2", "level": 8, "reason": "bad level"},
                {"indicator_id": "I2", "level": 2, "reason": ""},
            ],
        }],
        [{"work_unit_id": "WU_1", "project_id": "P1"}],
        {"I1", "I2"},
    )

    assert [(item["indicator_id"], item["level"], item["reason"]) for item in results] == [
        ("I1", 3, "supported")
    ]
    assert errors == [
        "work_unit_evaluation:WU_1:unknown_indicator",
        "work_unit_evaluation:WU_1:invalid_level",
        "work_unit_evaluation:WU_1:invalid_reason",
    ]


def test_project_indicator_normalizer_keeps_valid_items_and_ignores_inactive() -> None:
    results, errors = _normalize_project_results(
        {
            "project_id": "P1",
            "activated_indicators": [
                {"indicator_id": "I1", "level": "4", "reason": " project-wide "},
                {"indicator_id": "I2", "level": None, "reason": "inactive"},
                {"indicator_id": "BAD", "level": 3, "reason": "unknown"},
            ],
        },
        [{"work_unit_id": "WU_1", "project_id": "P1"}],
        {"I1", "I2"},
    )

    assert [(item["indicator_id"], item["level"]) for item in results] == [("I1", 4)]
    assert errors == ["project_indicator:unknown_indicator"]


def test_work_unit_gateway_retries_only_when_whole_batch_is_unusable() -> None:
    assert _evaluation_response(
        {"evaluations": [{"target_id": "UNKNOWN", "activated_indicators": []}]},
        {"WU_1"},
    ) == (False, "no_usable_target_evaluation")
    assert _evaluation_response(
        {"evaluations": [{"target_id": "WU_1", "activated_indicators": []}]},
        {"WU_1", "WU_2"},
    ) == (True, "")


def test_request_schema_binds_frozen_runtime_ids() -> None:
    schema = _schema_for_request(
        "work_unit_indicator",
        {"allowed_target_ids": ["WU_1"], "indicators": [{"indicator_id": "I1"}]},
    )
    target = schema["properties"]["evaluations"]["items"]["properties"]["target_id"]
    indicator = schema["properties"]["evaluations"]["items"]["properties"]["activated_indicators"]["items"]["properties"]["indicator_id"]
    assert target["enum"] == ["WU_1"]
    assert indicator["enum"] == ["I1"]

    project_schema = _schema_for_request(
        "project_indicator",
        {"project_id": "P1", "indicators": [{"indicator_id": "I1"}]},
    )
    assert project_schema["properties"]["project_id"]["const"] == "P1"
    assert project_schema["$defs"]["indicatorResult"]["properties"]["indicator_id"]["enum"] == ["I1"]
