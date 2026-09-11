import json

import pytest

from recruitment_ai_core.interview_evaluation import anchor_evaluation
from recruitment_ai_core.llm import LLMResponseError


def _units():
    return [
        {"unitId": "U1", "text": "能够说明方案取舍"},
        {"unitId": "U2", "text": "完成关键模块交付"},
    ]


def _anchors():
    return [
        {"nodeId": "A1", "anchorType": "job", "description": "方案设计"},
        {"nodeId": "A2", "anchorType": "job", "description": "工程交付"},
    ]


def test_partial_matrix_keeps_valid_rows_and_repairs_only_missing_pairs(
    monkeypatch,
) -> None:
    calls = []

    def fake_call(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return {
                "judgements": [
                    {"unitId": "U1", "anchorId": "A1", "judgement": "support"},
                    {"unitId": "UNKNOWN", "anchorId": "A1", "judgement": "support"},
                ]
            }, {"mode": "test"}
        request = json.loads(kwargs["messages"][1]["content"].split("\n", 1)[1])
        return {
            "judgements": [
                {**pair, "judgement": "not_support"}
                for pair in request["requiredPairs"]
            ]
        }, {"mode": "test"}

    monkeypatch.setattr(anchor_evaluation, "call_json_llm", fake_call)
    result = anchor_evaluation.evaluate_anchor_batch(
        units=_units(),
        anchors=_anchors(),
        workflow_name="post_first_scoring",
    )

    assert len(calls) == 2
    repair_request = json.loads(calls[1]["messages"][1]["content"].split("\n", 1)[1])
    assert repair_request["requiredPairs"] == [
        {"unitId": "U1", "anchorId": "A2"},
        {"unitId": "U2", "anchorId": "A1"},
        {"unitId": "U2", "anchorId": "A2"},
    ]
    assert result["coverageComplete"] is True
    assert result["expectedPairCount"] == result["validPairCount"] == 4
    assert result["judgements"][0] == {
        "unitId": "U1",
        "anchorId": "A1",
        "judgement": "support",
    }


def test_disabled_model_cannot_complete_as_an_empty_matrix(monkeypatch) -> None:
    calls = 0

    def disabled_call(**_kwargs):
        nonlocal calls
        calls += 1
        return None, {"mode": "disabled"}

    monkeypatch.setattr(anchor_evaluation, "call_json_llm", disabled_call)
    with pytest.raises(
        LLMResponseError, match="post_interview_anchor_model_unavailable"
    ):
        anchor_evaluation.evaluate_anchor_batch(
            units=[_units()[0]],
            anchors=[_anchors()[0]],
            workflow_name="post_first_scoring",
        )
    assert calls == 2


def test_incomplete_repair_is_reported_instead_of_published_as_zero(
    monkeypatch,
) -> None:
    responses = iter(
        [
            {
                "judgements": [
                    {"unitId": "U1", "anchorId": "A1", "judgement": "verified"},
                ]
            },
            {
                "judgements": [
                    {"unitId": "U1", "anchorId": "A2", "judgement": "not_support"},
                ]
            },
        ]
    )
    monkeypatch.setattr(
        anchor_evaluation,
        "call_json_llm",
        lambda **_kwargs: (next(responses), {"mode": "test"}),
    )

    result = anchor_evaluation.evaluate_anchor_batch(
        units=_units(),
        anchors=_anchors(),
        workflow_name="post_first_scoring",
    )

    assert result["coverageComplete"] is False
    assert result["validPairCount"] == 2
    assert result["missingPairs"] == [
        {"unitId": "U2", "anchorId": "A1"},
        {"unitId": "U2", "anchorId": "A2"},
    ]


def test_complete_all_not_support_matrix_is_a_valid_result(monkeypatch) -> None:
    captured = {}

    def complete_call(**kwargs):
        captured.update(kwargs)
        return {
            "judgements": [
                {"unitId": "U1", "anchorId": "A1", "judgement": "not_support"},
                {"unitId": "U1", "anchorId": "A2", "judgement": "not_support"},
            ]
        }, {"mode": "test"}

    monkeypatch.setattr(anchor_evaluation, "call_json_llm", complete_call)
    result = anchor_evaluation.evaluate_anchor_batch(
        units=[_units()[0]],
        anchors=_anchors(),
        workflow_name="post_second_scoring",
    )

    row_schema = captured["json_schema"]["properties"]["judgements"]["items"]
    system_prompt = captured["messages"][0]["content"]
    assert row_schema["required"] == ["unitId", "anchorId", "judgement"]
    assert row_schema["additionalProperties"] is False
    assert set(row_schema["properties"]["judgement"]["enum"]) == {
        "not_support",
        "partial",
        "support",
        "verified",
        "weak_contradiction",
        "contradicted",
        "strong_contradiction",
    }
    assert "partial 只能表达正向但不完整的证据" in system_prompt
    assert "才返回 strong_contradiction" in system_prompt
    assert "不能反证问题界定、方案执行或结果价值" in system_prompt
    assert "对其他无关能力返回 not_support" in system_prompt
    assert "每个配对必须先经过直接相关性门槛" in system_prompt
    assert "不得使用简历其他事实、上一版分数、行业常识" in system_prompt
    assert "项目经历丰富，涉及结构设计和仿真" in system_prompt
    assert "熟悉CAD并绘制工程图纸" in system_prompt
    assert "本科GPA较低，学术功底存疑" in system_prompt
    assert "对‘机械结构设计能力’返回 not_support" in system_prompt
    assert "对‘方案执行能力’必须返回 not_support" in system_prompt
    assert result["coverageComplete"] is True
    assert result["repairAttempted"] is False
    assert result["validPairCount"] == 2
