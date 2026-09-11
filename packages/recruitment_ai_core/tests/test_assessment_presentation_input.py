"""Regression coverage for the assessment presentation LLM input."""
from __future__ import annotations

from recruitment_ai_core.decision_summary.assessment_presentation_input import (
    build_assessment_presentation_inputs,
)


def _prepared() -> dict:
    return build_assessment_presentation_inputs(
        stage="screening",
        core_result={
            "score_result": {
                "total": 77.15,
                "job_fit": 74.84,
                "experience": 74.08,
                "education": 92.0,
            },
            "job_result": {
                "capability_results": [{
                    "job_capability_result_id": "JCR_001",
                    "job_capability_id": "JDC_001",
                    "primary_pair_id": "PAIR_001",
                }],
                "pair_assessments": [{
                    "pair_id": "PAIR_001",
                    "reason": "候选人在反应堆系统设计项目中负责安全分析与方案评审。",
                    "proof_work_unit_ids": ["WU_001"],
                }],
            },
        },
        rule_result={
            "strength_signals": [{
                "signal_key": "strength:job_capability:JDC_001",
                "source_type": "job_capability",
                "target_id": "JDC_001",
                "source_result_ids": ["JCR_001"],
            }],
            "weakness_signals": [],
        },
        job_profile={
            "job_capabilities": [{
                "job_capability_id": "JDC_001",
                "capability_name": "反应堆安全分析能力",
                "capability_definition": "识别安全边界、完成分析并形成可审查结论。",
            }],
        },
        interview_targets=[],
    )


def test_presentation_input_includes_only_four_scores_and_readable_fact_context() -> None:
    prepared = _prepared()
    bundle = prepared["bundle_input"]
    assert bundle["score_summary"] == {
        "total": 77.15,
        "job_fit": 74.84,
        "experience": 74.08,
        "education": 92.0,
    }
    signal = bundle["strengths"][0]
    assert signal["target_name"] == "反应堆安全分析能力"
    assert "JDC_001" not in signal["target_name"]
    assert "反应堆系统设计项目" in signal["result_summary"]
    assert "hard_screening" not in bundle
    assert "assessment_quality" not in bundle


def test_missing_score_remains_explicit_null_for_model_reasoning() -> None:
    prepared = _prepared()
    prepared = build_assessment_presentation_inputs(
        stage="screening",
        core_result={"score_result": {"total": 70, "job_fit": None, "experience": 68}},
        rule_result={}, job_profile={}, interview_targets=[],
    )
    assert prepared["bundle_input"]["score_summary"] == {
        "total": 70.0,
        "job_fit": None,
        "experience": 68.0,
        "education": None,
    }
