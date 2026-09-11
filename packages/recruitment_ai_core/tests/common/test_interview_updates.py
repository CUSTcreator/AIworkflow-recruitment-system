from __future__ import annotations

import json

import pytest

from recruitment_ai_core.common.interview_parse import (
    _merge_parse_results,
    build_record,
    build_question_bindings,
    build_question_contexts,
    normalize_parse_result,
    try_parse_interview_record,
)
from recruitment_ai_core.common.scoring_evidence import apply_interview_parse_result
from recruitment_ai_core.screening_scoring.resume_experience.pipeline import reassess_resume_experience


def test_obvious_hr_note_is_non_scoring_without_llm(monkeypatch) -> None:
    def fail_if_called(**kwargs):
        del kwargs
        raise AssertionError("LLM should not parse obvious HR-only information")

    monkeypatch.setattr(
        "recruitment_ai_core.common.interview_parse.call_json_llm",
        fail_if_called,
    )
    parsed, trace, warnings = try_parse_interview_record(
        application_id="A",
        stage="after_second_interview",
        evidence_units=[{
            "evidence_unit_id": "IEU_1",
            "source_type": "hr_free_note",
            "source_interview_record_id": "IR_1",
            "source_interview_round_id": "ROUND_1",
            "raw_text": "候选人愿意入职，可两周内到岗，期望薪资在预算范围内。",
        }],
        scoring_evidence={},
        job_profile={},
        question_bindings={},
        llm_config={"enabled": True},
    )
    assert trace["mode"] == "needs_review_after_parse_error"
    assert warnings and warnings[0].startswith("interview_parse_needs_review:")
    assert parsed is not None
    assert parsed["interview_observations"] == []
    assert parsed["non_scoring"] == []
    assert parsed["review_required"] is True
    assert parsed["review_records"][0]["record_id"] == "IR_1"


def _snapshot() -> dict:
    return {

        "projects": [{"project_id": "P1", "name": "检索系统"}],
        "work_units": [{
            "work_unit_id": "WU1",
            "work_unit_version_id": "WU1_v1",
            "project_id": "P1",
            "revision": 1,
            "previous_version_id": None,
            "is_current": True,
            "action": "构建",
            "object": "检索链路",
            "methods": ["向量检索"],
            "result": None,
            "metrics": [],
            "source_refs": [{"bullet_id": "B1", "quote": "构建向量检索链路"}],
        }],
        "skill_claims": [{"skill_claim_id": "SC1", "raw_text": "熟悉 Redis"}],
        "skill_claim_states": [{"skill_claim_id": "SC1", "effective": True}],
        "interview_observations": [],
        "source_interview_record_ids": [],
    }


def _job_profile() -> dict:
    return {"job_capabilities": [{"job_capability_id": "JC1"}]}


def _v3_non_scoring_response(
    record_id: str, source_quote: str, *, source_segment_id: str = "E1"
) -> dict:
    return {
        "record_id": record_id,
        "segments": [{
            "segment_id": "SEG_RESULT_1",
            "category": "non_scoring",
            "raw_text": source_quote,
            "source_refs": [{
                "segment_id": source_segment_id,
                "quote": source_quote,
            }],
            "resolution_status": "resolved",
            "review_reason": None,
            "target_decisions": [],
        }],
        "experience_updates": [],
        "skill_claim_corrections": [],
        "interview_observations": [],
        "non_scoring": [{
            "source_refs": [{
                "segment_id": "SEG_RESULT_1",
                "quote": source_quote,
            }],
            "reason_code": "unrelated",
            "polarity": "neutral",
            "summary": "无关",
        }],
    }


def test_parse_and_apply_updates_create_immutable_work_unit_revision() -> None:
    record = {
        "record_id": "IR_A_after_first_interview",
        "interview_round_id": "ROUND_A_FIRST",
        "record_type": "question_answer",
        "interviewer_judgement": "verified",
        "segments": [
            {"segment_id": "S1", "text": "候选人补充说明采用 BM25 与向量混合检索。"},
            {"segment_id": "S2", "text": "候选人现场完成了缓存击穿处理。"},
            {"segment_id": "S3", "text": "候选人澄清并不熟悉 Redis。"},
        ],
    }
    payload = {
        "record_id": record["record_id"],
        "experience_updates": [{
            "update_type": "supplement",
            "target_work_unit_id": "WU1",
            "project_id": "P1",
            "new_project": None,
            "source_refs": [{"segment_id": "S1", "quote": "采用 BM25 与向量混合检索"}],
            "resulting_work_unit": {
                "raw_text": "构建采用 BM25 与向量混合检索的检索链路",
                "source_refs": [{"segment_id": "S1", "quote": "采用 BM25 与向量混合检索"}],
            },
        }],
        "skill_claim_corrections": [{
            "skill_claim_id": "SC1",
            "source_refs": [{"segment_id": "S3", "quote": "并不熟悉 Redis"}],
            "reason": "候选人明确撤回技能声明",
        }],
        "interview_observations": [{
            "observation_key": "obs1",
            "question_id": "Q1",
            "scenario_key": None,
            "observation_mode": "reasoning",
            "interviewer_judgement": "verified",
            "interviewer_note": "候选人现场完成了缓存击穿处理",
            "source_refs": [{"segment_id": "S2", "quote": "现场完成了缓存击穿处理"}],
            "target_job_capability_ids": ["JC1"],
            "target_preset_indicator_ids": [],
        }],
        "non_scoring": [],
    }
    parsed = normalize_parse_result(
        payload,
        record=record,
        snapshot=_snapshot(),
        job_profile=_job_profile(),
        question_bindings={"Q1": {"target_job_capability_ids": ["JC1"], "target_preset_indicator_ids": []}},
        application_id="A",
        stage="after_first_interview",
    )
    scoring_evidence = _snapshot()
    apply_interview_parse_result(scoring_evidence, parsed)

    versions = scoring_evidence["work_units"]
    assert versions[0]["is_current"] is False
    assert versions[1]["work_unit_version_id"] == "WU1_v2"
    assert versions[1]["previous_version_id"] == "WU1_v1"
    assert versions[1]["raw_text"] == "构建采用 BM25 与向量混合检索的检索链路"
    assert versions[1]["source_refs"][0]["bullet_id"] == "B1"
    assert versions[1]["source_refs"][1]["segment_id"] == "S1"
    assert scoring_evidence["affected_project_ids"] == ["P1"]
    assert scoring_evidence["corrected_skill_claim_ids"] == ["SC1"]
    assert scoring_evidence["excluded_skill_claim_ids"] == ["SC1"]
    assert len(scoring_evidence["interview_observations"]) == 1


def test_source_quote_must_be_contiguous_verbatim_text() -> None:
    record = {"record_id": "IR", "segments": [{"segment_id": "S1", "text": "采用 BM25 与向量混合检索"}]}
    payload = {
        "record_id": "IR",
        "experience_updates": [],
        "skill_claim_corrections": [],
        "interview_observations": [],
        "non_scoring": [{
            "source_refs": [{"segment_id": "S1", "quote": "采用 BM25……混合检索"}],
            "reason_code": "unrelated",
            "polarity": "neutral",
            "summary": "无关内容",
        }],
    }
    with pytest.raises(ValueError, match="source_quote_not_contiguous"):
        normalize_parse_result(
            payload,
            record=record,
            snapshot=_snapshot(),
            job_profile=_job_profile(),
            question_bindings={},
            application_id="A",
            stage="after_first_interview",
        )


def test_merged_parse_result_only_keeps_persisted_source_record_ids() -> None:
    merged = _merge_parse_results(
        "A",
        "after_first_interview",
        [{
            "record_id": "IR_DERIVED_PARSE_FRAGMENT",
            "source_record_ids": ["IR_PERSISTED"],
            "interview_round_id": "ROUND_1",
            "experience_updates": [],
            "skill_claim_corrections": [],
            "interview_observations": [],
            "non_scoring": [],
        }],
    )
    assert merged["record_id"].startswith("ROUND_RECORDS_")
    assert merged["source_record_ids"] == ["IR_PERSISTED"]


def test_runtime_scoring_evidence_keeps_only_persisted_source_record_ids() -> None:
    scoring_evidence = _snapshot()
    apply_interview_parse_result(
        scoring_evidence,
        {
            "record_id": "ROUND_RECORDS_DERIVED",
            "source_record_ids": ["IR_PERSISTED"],
            "experience_updates": [],
            "skill_claim_corrections": [],
            "interview_observations": [],
        },
    )
    assert scoring_evidence["source_interview_record_ids"] == ["IR_PERSISTED"]


def test_same_source_cannot_update_experience_and_score_observation() -> None:
    record = {"record_id": "IR", "segments": [{"segment_id": "S1", "text": "补充使用 BM25"}]}
    ref = [{"segment_id": "S1", "quote": "使用 BM25"}]
    payload = {
        "record_id": "IR",
        "experience_updates": [{
            "update_type": "supplement", "target_work_unit_id": "WU1",
            "project_id": "P1", "new_project": None, "source_refs": ref,
            "resulting_work_unit": {"raw_text": "补充使用 BM25", "source_refs": ref},
        }],
        "skill_claim_corrections": [],
        "interview_observations": [{
            "observation_key": "obs", "question_id": None,
            "scenario_key": None, "observation_mode": "knowledge",
            "interviewer_judgement": "support", "interviewer_note": "说明 BM25",
            "source_refs": ref, "target_job_capability_ids": ["JC1"],
            "target_preset_indicator_ids": [],
        }],
        "non_scoring": [],
    }
    with pytest.raises(ValueError, match="duplicate_experience_and_observation_source"):
        normalize_parse_result(
            payload, record=record, snapshot=_snapshot(), job_profile=_job_profile(),
            question_bindings={}, application_id="A", stage="after_first_interview",
        )


def test_only_affected_resume_project_is_rebuilt() -> None:
    snapshot = _snapshot()
    snapshot["project_assessments"] = [{
        "project_id": "P2", "project_name": "unchanged",
        "work_units": [],
        "project_indicator_results": [],
    }]
    snapshot["affected_project_ids"] = ["P1"]
    result = reassess_resume_experience(snapshot, {"enabled": False})

    assert result is not None
    assert result["change_summary"]["affected_project_ids"] == ["P1"]
    assert {item["project_id"] for item in result["project_experience_assessments"]} == {"P1", "P2"}
    assert snapshot["project_assessments"] == result["project_experience_assessments"]
    active = next(item for item in snapshot["work_units"] if item["is_current"])
    assert "work_unit_indicator_results" in active
    assert snapshot["project_evidence"]


def test_semantic_validation_retries_once(monkeypatch) -> None:
    class Settings:
        fail_open = True

    calls = []
    record_id = build_record("A", "after_first_interview", [{"evidence_unit_id": "E1", "raw_text": "有效原文"}])["record_id"]
    responses = [
        {"record_id": record_id, "experience_updates": [], "skill_claim_corrections": [], "interview_observations": [], "non_scoring": [{"source_refs": [{"segment_id": "E1", "quote": "不存在"}], "reason_code": "unrelated", "polarity": "neutral", "summary": "无关"}]},
        {"record_id": record_id, "experience_updates": [], "skill_claim_corrections": [], "interview_observations": [], "non_scoring": [{"source_refs": [{"segment_id": "E1", "quote": "有效原文"}], "reason_code": "unrelated", "polarity": "neutral", "summary": "无关"}]},
    ]
    source_text = build_record(
        "A", "after_first_interview",
        [{"evidence_unit_id": "E1", "raw_text": "鏈夋晥鍘熸枃"}],
    )["segments"][0]["text"]
    source_text = responses[1]["non_scoring"][0]["source_refs"][0]["quote"]
    responses = [
        _v3_non_scoring_response(record_id, "not-in-source"),
        _v3_non_scoring_response(record_id, source_text),
    ]
    monkeypatch.setattr("recruitment_ai_core.common.interview_parse.load_llm_settings", lambda _config: Settings())
    monkeypatch.setattr(
        "recruitment_ai_core.common.interview_parse.call_json_llm",
        lambda **kwargs: (responses[len(calls)], calls.append(kwargs) or {"status": "success"}),
    )
    result, trace, warnings = try_parse_interview_record(
        application_id="A", stage="after_first_interview",
        evidence_units=[{"evidence_unit_id": "E1", "raw_text": "有效原文"}],
        scoring_evidence=_snapshot(), job_profile=_job_profile(),
        question_bindings={}, llm_config={"enabled": True},
    )

    assert result is not None
    assert len(calls) == 2
    assert trace["semantic_attempt"] == 2
    assert warnings == []
    assert "source_quote_not_contiguous" in calls[1]["messages"][1]["content"]


def test_focus_area_binding_resolves_to_existing_job_capability() -> None:
    bindings = build_question_bindings(
        [{"question_id": "Q2", "source_focus_area_ids": ["FA1"]}],
        {"job_capabilities": [{"job_capability_id": "JC1"}]},
        interview_targets=[{"interview_target_id": "T1", "risk_ids": ["R1"], "target_ids": ["JC1"]}],
        focus_areas=[{"focus_area_id": "FA1", "source_objects": [{"source_type": "risk_update", "source_id": "R1"}]}],
    )
    assert bindings == {"Q2": ["JC1"]}


def test_question_context_keeps_capability_and_source_evidence_lineage() -> None:
    contexts = build_question_contexts(
        [{
            "question_id": "Q3",
            "interview_target_ids": ["T1"],
            "question_type": "project_walkthrough",
            "purpose": "核验项目指标口径",
        }],
        {"job_capabilities": [{"job_capability_id": "JC1"}]},
        interview_targets=[{
            "interview_target_id": "T1",
            "purpose": "verify_experience",
            "target_ids": ["JC1"],
            "source_evidence_ids": ["WU1_v1"],
        }],
    )

    assert contexts == {
        "Q3": {
            "target_job_capability_ids": ["JC1"],
            "target_preset_indicator_ids": [],
            "interview_target_ids": ["T1"],
            "source_evidence_ids": ["WU1_v1"],
            "purposes": ["核验项目指标口径", "verify_experience"],
            "purpose": "核验项目指标口径",
            "scenario_id": None,
            "evaluation_rubrics": [],
            "question_type": "project_walkthrough",
        },
    }


def test_interview_prompt_resolves_evidence_version_to_active_work_unit(
    monkeypatch,
) -> None:
    class Settings:
        fail_open = True

    calls = []
    record_id = build_record("A", "after_first_interview", [{"evidence_unit_id": "E1", "raw_text": "有效原文"}])["record_id"]
    monkeypatch.setattr(
        "recruitment_ai_core.common.interview_parse.load_llm_settings",
        lambda _config: Settings(),
    )
    monkeypatch.setattr(
        "recruitment_ai_core.common.interview_parse.call_json_llm",
        lambda **kwargs: (
            {
                    "record_id": record_id,
                "experience_updates": [],
                "skill_claim_corrections": [],
                "interview_observations": [],
                "non_scoring": [],
            },
            calls.append(kwargs) or {"status": "success"},
        ),
    )

    result, _, _ = try_parse_interview_record(
        application_id="A",
        stage="after_first_interview",
        evidence_units=[{"evidence_unit_id": "E1", "raw_text": "有效原文"}],
        scoring_evidence=_snapshot(),
        job_profile=_job_profile(),
        question_bindings={
            "Q1": {
                "target_job_capability_ids": ["JC1"],
                "source_evidence_ids": ["WU1_v1"],
            },
        },
        llm_config={"enabled": True},
    )

    assert result is not None
    prompt_body = json.loads(calls[0]["messages"][1]["content"])
    assert prompt_body["question_bindings"]["Q1"]["source_work_unit_ids"] == ["WU1"]
