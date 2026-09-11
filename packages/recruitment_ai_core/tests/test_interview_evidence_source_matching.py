from __future__ import annotations

from recruitment_ai_core.interview_evaluation import pipeline
from recruitment_ai_core.interview_evaluation import (
    InterviewParseInput,
    extract_and_bind_interview_evidence,
    normalize_interview_evidence,
)


def _input(source: str, item_text: str) -> InterviewParseInput:
    return InterviewParseInput(
        stage="after_second_interview",
        interview_records=[
            {
                "record_id": "IR_SOURCE",
                "record_type": "free_note",
                "raw_text": source,
                "payload": {
                    "semantic_evidence": {
                        "items": [
                            {
                                "record_id": "IR_SOURCE",
                                "text": item_text,
                                "is_capability_evaluation": True,
                                "is_experience_related": True,
                            }
                        ]
                    }
                },
            }
        ],
        resume_profile={},
        job_requirement_profile={},
        open_targets=[],
    )


def test_source_binding_tolerates_spacing_and_punctuation_but_keeps_original_quote() -> None:
    source = "候选人能够完成多物理场耦合分析，工程经验扎实。"
    extracted = extract_and_bind_interview_evidence(
        _input(source, "候选人能够完成多物理场耦合分析, 工程经验扎实")
    )

    assert len(extracted.items) == 1
    assert extracted.items[0].text == source.rstrip("。")
    assert extracted.items[0].source_refs[0]["quote"] == source.rstrip("。")


def test_source_binding_relocates_minor_paraphrase_to_original_clause() -> None:
    source = "软件技能丰富，论文专利成果突出，工程经验扎实。英语沟通能力一般。"
    extracted = extract_and_bind_interview_evidence(
        _input(source, "软件能力丰富，论文和专利成果突出，工程实践经验扎实")
    )
    draft = normalize_interview_evidence(_input(source, extracted.items[0].text), extracted)

    assert len(extracted.items) == 1
    assert extracted.items[0].text == "软件技能丰富，论文专利成果突出，工程经验扎实。"
    assert draft.review_required is False
    assert draft.assertions[0]["sourceQuote"] == extracted.items[0].text


def test_unrelated_model_text_is_dropped_without_blocking_the_round() -> None:
    source = "候选人能够完成多物理场耦合分析。"
    extracted = extract_and_bind_interview_evidence(
        _input(source, "候选人精通财务审计和税务筹划。")
    )
    draft = normalize_interview_evidence(_input(source, "irrelevant"), extracted)

    assert extracted.items == ()
    assert draft.assertions == ()
    assert draft.review_required is False


def test_experience_route_cannot_bypass_the_capability_evaluation_gate() -> None:
    source = "建议进入下一轮，后续再确认项目细节。"
    input_data = _input(source, source)
    input_data.interview_records[0]["payload"]["semantic_evidence"]["items"][0].update(
        {
            "is_capability_evaluation": False,
            "is_experience_related": True,
        }
    )

    extracted = extract_and_bind_interview_evidence(input_data)
    draft = normalize_interview_evidence(input_data, extracted)

    assert extracted.items[0].is_capability_evaluation is False
    assert extracted.items[0].is_experience_related is False
    assert draft.assertions[0]["isExperienceRelated"] is False


def test_extraction_prompt_requires_atomic_topic_and_polarity_units(monkeypatch) -> None:
    captured = {}
    source = "本硕均为985/211，项目经历丰富，已发表论文。"
    input_data = _input(source, source)
    input_data.interview_records[0]["payload"] = {}

    def fake_call(**kwargs):
        captured.update(kwargs)
        return {
            "items": [
                {
                    "record_id": "IR_SOURCE",
                    "text": "本硕均为985/211",
                    "is_capability_evaluation": True,
                    "is_experience_related": False,
                },
                {
                    "record_id": "IR_SOURCE",
                    "text": "项目经历丰富",
                    "is_capability_evaluation": True,
                    "is_experience_related": True,
                },
                {
                    "record_id": "IR_SOURCE",
                    "text": "已发表论文",
                    "is_capability_evaluation": True,
                    "is_experience_related": False,
                },
            ]
        }, {"mode": "test"}

    monkeypatch.setattr(pipeline, "call_json_llm", fake_call)
    result = extract_and_bind_interview_evidence(input_data)

    prompt = captured["messages"][1]["content"]
    item_schema = captured["json_schema"]["properties"]["items"]["items"]
    assert "一个评价对象、一个评价主题和一个评价方向" in prompt
    assert "正向和负向评价也必须拆开" in prompt
    assert "至少应按教育背景、项目经历和论文成果拆成三个 item" in prompt
    assert set(item_schema["properties"]) == {
        "record_id",
        "text",
        "is_capability_evaluation",
        "is_experience_related",
    }
    assert [item.text for item in result.items] == [
        "本硕均为985/211",
        "项目经历丰富",
        "已发表论文",
    ]
