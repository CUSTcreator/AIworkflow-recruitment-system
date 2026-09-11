from __future__ import annotations

import json

from recruitment_ai_core.first_interview_planning import (
    build_question_planning_constraints,
    try_generate_question_proposals,
)
from recruitment_ai_core.first_interview_planning.contracts import (
    FirstInterviewPlanningInput,
)
from recruitment_ai_core.first_interview_planning import llm_adapter


def _input(targets: list[dict] | None = None) -> FirstInterviewPlanningInput:
    return FirstInterviewPlanningInput(
        application_id="APP_TEST",
        candidate_id="CAND_TEST",
        job_id="JOB_TEST",
        job_title="Agent 开发实习生",
        job_profile={
            "job_capabilities": [
                {
                    "job_capability_id": "JDC_001",
                    "capability_name": "后端 API 与服务开发",
                    "capability_definition": "能设计并实现可维护的后端服务",
                    "assessment_mode": "experience",
                },
                {
                    "job_capability_id": "JDC_002",
                    "capability_name": "现场方案设计",
                    "capability_definition": "能完成现场工程方案拆解",
                    "assessment_mode": "live",
                },
            ]
        },
        evidence_records=[
            {"evidence_id": "WU_001", "evidence_type": "work_unit", "raw_text": "实现了异步任务调度与失败重试。"}
        ],
        interview_targets=targets or [
            {
                "interview_target_id": "IT_001",
                "purpose": "verify_experience",
                "target_type": "job_capability",
                "target_id": "JDC_001",
                "title": "后端 API 与服务开发",
                "verification_goal": "核验个人实现深度和工程结果。",
                "trigger_code": "high_result_narrow_proof",
                "evidence_ids": ["WU_001"],
                "status": "open",
            },
            {
                "interview_target_id": "IT_002",
                "purpose": "direct_demonstration",
                "target_type": "job_capability",
                "target_id": "JDC_002",
                "title": "现场方案设计",
                "verification_goal": "现场展示任务拆分和异常处理。",
                "trigger_code": "configured_live_assessment",
                "status": "open",
            },
        ],
    )


def test_constraint_set_covers_all_open_targets_and_only_allocates_bound_extra_slots():
    constraints = build_question_planning_constraints(_input())

    assert [item["interview_target_id"] for item in constraints.target_snapshots] == ["IT_001", "IT_002"]
    assert {item["interview_target_ids"][0] for item in constraints.question_slots} == {"IT_001", "IT_002"}
    assert all(item["interview_target_ids"] for item in constraints.question_slots)
    assert all(item["source_evidence_ids"] == ["WU_001"] or item["interview_target_ids"] == ["IT_002"] for item in constraints.question_slots)
    direct_slots = [item for item in constraints.question_slots if item["interview_target_ids"] == ["IT_002"]]
    assert direct_slots[0]["question_type"] == "direct_task"
    assert direct_slots[0]["expected_result_type"] == "interview_performance"
    assert direct_slots[0]["base_rubrics"]


def test_llm_receives_all_frozen_targets_and_returns_slot_bound_proposals(monkeypatch):
    targets = [
        {
            "interview_target_id": f"IT_{index}",
            "purpose": "verify_experience",
            "target_type": "job_capability",
            "target_id": "JDC_001",
            "title": f"能力 {index}",
            "verification_goal": f"核验能力 {index}",
            "status": "open",
        }
        for index in range(1, 6)
    ]
    planning_input = _input(targets)
    constraints = build_question_planning_constraints(planning_input)
    captured: dict = {}

    def fake_call_json_llm(**kwargs):
        captured.update(kwargs)
        return {
            "schema_version": llm_adapter.PLANNING_LLM_SCHEMA_VERSION,
            "question_proposals": [
                {
                    "slot_id": constraints.question_slots[0]["slot_id"],
                    "question_text": "请说明你负责的核心模块。",
                    "follow_up_questions": ["你如何验证结果？"],
                    "expected_evidence": ["个人动作", "结果证据"],
                    "evaluation_rubrics": [],
                }
            ],
        }, {"status": "success", "duration_ms": 1200}

    monkeypatch.setattr(llm_adapter, "call_json_llm", fake_call_json_llm)
    result = try_generate_question_proposals(planning_input, constraints)

    payload = json.loads(captured["messages"][1]["content"])
    assert len(payload["open_interview_targets"]) == 5
    assert len(payload["question_slots"]) >= 5
    assert result.generation_mode == "llm"
    assert result.proposals[0].slot_id == constraints.question_slots[0]["slot_id"]
    assert result.proposals[0].question_text == "请说明你负责的核心模块。"