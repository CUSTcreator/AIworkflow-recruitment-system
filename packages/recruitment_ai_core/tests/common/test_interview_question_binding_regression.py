from recruitment_ai_core.common.interview_parse import normalize_parse_result


def test_confirmed_reasoning_question_keeps_bound_targets() -> None:
    record = {
        "record_id": "IR_REASONING",
        "interview_round_id": "ROUND_FIRST",
        "record_type": "question_answer",
        "question_id": "Q_REASONING",
        "answer_status": "answered",
        "interviewer_judgement": "verified",
        "segments": [{"segment_id": "S1", "text": "能够说明问题边界和方案取舍"}],
    }
    payload = {
        "record_id": record["record_id"],
        "experience_updates": [],
        "skill_claim_corrections": [],
        "interview_observations": [{
            "observation_key": "obs_reasoning",
            "question_id": "Q_REASONING",
            "scenario_key": None,
            "observation_mode": "reasoning",
            "interviewer_judgement": "verified",
            "interviewer_note": "能够说明问题边界和方案取舍",
            "source_refs": [{"segment_id": "S1", "quote": "能够说明问题边界和方案取舍"}],
            "target_job_capability_ids": [],
            "target_preset_indicator_ids": [],
        }],
        "non_scoring": [],
    }
    parsed = normalize_parse_result(
        payload,
        record=record,
        snapshot={"work_unit_versions": [], "skill_claim_states": []},
        job_profile={
            "job_capabilities": [{"job_capability_id": "JC1"}],
            "preset_model_id": "engineering_experience",
        },
        question_bindings={
            "Q_REASONING": {
                "target_job_capability_ids": ["JC1"],
                "target_preset_indicator_ids": ["problem_definition_focus"],
            }
        },
        application_id="A",
        stage="after_first_interview",
    )
    observation = parsed["interview_observations"][0]
    assert observation["target_job_capability_ids"] == ["JC1"]
    assert observation["target_preset_indicator_ids"] == [
        "problem_definition_focus"
    ]