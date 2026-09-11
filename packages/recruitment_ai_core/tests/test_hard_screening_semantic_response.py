from __future__ import annotations

import pytest

from recruitment_ai_core.hard_screening import pipeline
from recruitment_ai_core.llm.schema_validator import (
    JSONSchemaValidationError,
    validate_json_schema,
)


class _EnabledSettings:
    def is_enabled_for(self, workflow_name: str) -> bool:
        return workflow_name == "hard_screening"


def _rule(rule_id: str) -> dict[str, object]:
    return {
        "rule_id": rule_id,
        "name": rule_id,
        "source_scope": "full_resume",
        "operator": "semantic_match",
        "expected_value": "具备 Python 开发经验",
    }


def test_semantic_schema_requires_evidence_for_automatic_decisions() -> None:
    passed_without_evidence = {
        "results": [
            {
                "rule_id": "rule_1",
                "status": "passed",
                "reason": "满足要求",
                "source_quotes": [],
            }
        ]
    }
    manual_review_without_evidence = {
        "results": [
            {
                "rule_id": "rule_1",
                "status": "manual_review",
                "reason": "信息不足",
                "source_quotes": [],
            }
        ]
    }

    with pytest.raises(JSONSchemaValidationError):
        validate_json_schema(
            passed_without_evidence,
            pipeline.HARD_SCREENING_SEMANTIC_RESPONSE_SCHEMA,
        )
    validate_json_schema(
        manual_review_without_evidence,
        pipeline.HARD_SCREENING_SEMANTIC_RESPONSE_SCHEMA,
    )


def test_duplicate_or_missing_rule_results_require_manual_review(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_call_json_llm(**kwargs):
        captured.update(kwargs)
        return {
            "results": [
                {
                    "rule_id": "rule_1",
                    "status": "passed",
                    "reason": "有 Python 经历",
                    "source_quotes": ["Python"],
                },
                {
                    "rule_id": "rule_1",
                    "status": "failed",
                    "reason": "重复返回的错误结果",
                    "source_quotes": ["Python"],
                },
            ]
        }, {}

    monkeypatch.setattr(pipeline, "load_llm_settings", lambda _config: _EnabledSettings())
    monkeypatch.setattr(pipeline, "call_json_llm", fake_call_json_llm)

    result = pipeline.evaluate_hard_screening(
        application_id="APP_1",
        resume_text="候选人具备 Python 开发经验。",
        rules=[_rule("rule_1"), _rule("rule_2")],
    )

    assert "每个输入rule_id必须恰好返回一次" in captured["messages"][0]["content"]
    assert result["status"] == "manual_review"
    assert [item["status"] for item in result["rule_results"]] == [
        "manual_review",
        "manual_review",
    ]


def test_exact_semantic_rule_results_can_pass(monkeypatch) -> None:
    def fake_call_json_llm(**_kwargs):
        return {
            "results": [
                {
                    "rule_id": "rule_1",
                    "status": "passed",
                    "reason": "有 Python 经历",
                    "source_quotes": ["Python"],
                },
                {
                    "rule_id": "rule_2",
                    "status": "passed",
                    "reason": "有开发经验",
                    "source_quotes": ["开发经验"],
                },
            ]
        }, {}

    monkeypatch.setattr(pipeline, "load_llm_settings", lambda _config: _EnabledSettings())
    monkeypatch.setattr(pipeline, "call_json_llm", fake_call_json_llm)

    result = pipeline.evaluate_hard_screening(
        application_id="APP_1",
        resume_text="候选人具备 Python 开发经验。",
        rules=[_rule("rule_1"), _rule("rule_2")],
    )

    assert result["status"] == "passed"
    assert [item["status"] for item in result["rule_results"]] == ["passed", "passed"]


def test_certification_scope_uses_only_structured_facts(monkeypatch) -> None:
    def fake_call_json_llm(**kwargs):
        source = kwargs["messages"][1]["content"]
        assert "英语等级；六级" in source
        assert "CET-6" in source
        return ({
            "results": [{
                "rule_id": "cet6",
                "status": "passed",
                "reason": "已通过英语六级",
                "source_quotes": ["英语等级", "六级"],
            }]
        }, {})

    monkeypatch.setattr(pipeline, "load_llm_settings", lambda _config: _EnabledSettings())
    monkeypatch.setattr(pipeline, "call_json_llm", fake_call_json_llm)
    result = pipeline.evaluate_hard_screening(
        application_id="APP_CERT",
        resume_text="原始文本不参与证书栏目回退解析",
        resume_profile={
            "candidate_facts": {
                "qualification_records": [{
                    "raw_text": "英语等级；六级",
                    "source_refs": [{"block_id": "B_1", "quote": "六级"}],
                }]
            }
        },
        rules=[{
            "rule_id": "cet6",
            "name": "证书/资质",
            "source_scope": "certifications",
            "operator": "semantic_match",
            "expected_value": "CET-6",
        }],
    )

    assert result["status"] == "passed"


def test_missing_structured_certification_requires_manual_review(monkeypatch) -> None:
    monkeypatch.setattr(pipeline, "load_llm_settings", lambda _config: _EnabledSettings())

    result = pipeline.evaluate_hard_screening(
        application_id="APP_CERT",
        resume_text=(
            "<table><tr><td>英语等级</td><td>六级</td>"
            "<td>英语等级成绩</td><td>520</td></tr></table>"
        ),
        resume_profile={"candidate_facts": {}},
        rules=[{
            "rule_id": "cet6",
            "name": "证书/资质",
            "source_scope": "certifications",
            "operator": "semantic_match",
            "expected_value": "CET-6",
        }],
    )

    assert result["status"] == "manual_review"
    assert result["rule_results"][0]["status"] == "manual_review"
    assert result["rule_results"][0]["reason_code"] == "source_fact_missing"
