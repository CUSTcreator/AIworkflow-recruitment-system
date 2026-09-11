from __future__ import annotations

from recruitment_ai_core.job_capability import incremental


def test_observation_update_preserves_existing_resume_evidence_judgement(monkeypatch):
    capability = {
        "job_capability_id": "CAP_1",
        "job_unit_id": "JDU_1",
        "capability_name": "Agent稳定性设计",
        "capability_definition": "处理幂等、重试和失败补偿",
        "required_evidence_elements": ["处理幂等与重试"],
        "role": "core",
        "evidence_mode": "atomic",
    }
    resume_evidence = {
        "pair_id": "PAIR_CAP_1_WU_1",
        "job_capability_id": "CAP_1",
        "evidence_type": "work_unit",
        "evidence_id": "WU_1",
        "content_level": 4,
        "content_score": 0.87,
        "pair_score": 0.87,
        "proof_work_unit_ids": ["WU_1"],
    }
    previous_capability = {
        "job_capability_result_id": "JCR_CAP_1",
        "job_capability_id": "CAP_1",
        "score": 0.87,
        "primary_pair_id": "PAIR_CAP_1_WU_1",
        "supplemental_pair_ids": [],
    }
    job_profile = {
        "job_capabilities": [capability],
            "jd_units": [
                {
                    "job_unit_id": "JDU_1",
                    "source_order": 1,
                    "section": "responsibility",
                    "raw_text": "处理幂等、重试和失败补偿",
                    "scoring_role": "required",
                    "capabilities": [capability],
                }
        ],
        "assessment_units": [],
        "capability_groups": [],
    }
    previous = {
        "application_id": "APP_1",
        "profile_version": "CCP_APP_1_SCREENING_V1",
        "stage": "screening",
        "job_profile": job_profile,
        "pair_assessments": [resume_evidence],
        "job_capability_results": [previous_capability],
        "job_unit_results": [],
        "job_capability_fit_assessments": [],
        "score_summary": {"job_requirement_score": 87.0},
    }
    observation = {
        "observation_id": "IO_1",
        "interview_round_id": "INT_APP_1_FIRST",
        "target_job_capability_ids": ["CAP_1"],
        "target_preset_indicator_ids": [],
        "observation_mode": "reasoning",
        "interviewer_judgement": "support",
        "status": "active",
    }
    target_result = {
        "result_id": "OTR_1",
        "observation_id": "IO_1",
        "target_type": "job_capability",
        "target_id": "CAP_1",
        "job_unit_id": "JDU_1",
        "level": 3,
        "score": 0.77,
        "interviewer_judgement": "support",
        "scenario_id": None,
    }
    round_evidence = {
        "round_evidence_id": "IRE_1",
        "interview_round_id": "INT_APP_1_FIRST",
        "target_type": "job_unit",
        "target_id": "JDU_1",
        "positive_score": 0.77,
        "negative_score": None,
        "positive_judgement": "support",
        "negative_judgement": None,
        "observation_result_ids": ["OTR_1"],
        "conflict_target_ids": [],
    }

    result = incremental.update_job_capability_with_interview(
        previous_result=previous,
        resume_profile={},
        interview_evidence=[observation],
        new_profile_version="CCP_APP_1_AFTER_FIRST_V2",
        stage="after_first_interview",
        scoring_evidence={
            "affected_project_ids": [],
            "corrected_skill_claim_ids": [],
            "interview_observations": [observation],
        },
        observation_target_results=[target_result],
        interview_round_evidence=[round_evidence],
    )

    updated = result["job_capability_results"][0]
    assert updated["score"] == 0.87
    assert updated["interview_target_results"][0]["result_id"] == "OTR_1"
    assert result["pair_assessments"][0]["pair_id"] == "PAIR_CAP_1_WU_1"
    assert result["job_unit_results"][0]["score"] == 0.874004
    assert result["score_summary"]["job_capability_fit_score"] == 87.4


def test_project_update_recalculates_all_fixed_job_targets_for_new_evidence(
    monkeypatch,
):
    capabilities = [
        {
            "job_capability_id": capability_id,
            "job_unit_id": f"JDU_{index}",
            "capability_name": capability_id,
            "capability_definition": capability_id,
            "required_evidence_elements": [capability_id],
            "role": "core",
            "evidence_mode": "atomic",
        }
        for index, capability_id in enumerate(("CAP_1", "CAP_2"), start=1)
    ]
    previous_evidence = [
        {
            "pair_id": f"PAIR_{capability['job_capability_id']}",
            "job_capability_id": capability["job_capability_id"],
            "evidence_type": "work_unit",
            "evidence_id": f"WU_{index}",
            "content_level": 3,
            "content_score": 0.77,
            "pair_score": 0.77,
            "proof_work_unit_ids": [f"WU_{index}"],
        }
        for index, capability in enumerate(capabilities, start=1)
    ]
    previous_results = [
        {
            "job_capability_result_id": f"JCR_{capability['job_capability_id']}",
            "job_capability_id": capability["job_capability_id"],
            "score": 0.77,
            "primary_pair_id": previous_evidence[index]["pair_id"],
            "supplemental_pair_ids": [],
        }
        for index, capability in enumerate(capabilities)
    ]
    job_profile = {
        "job_capabilities": capabilities,
        "jd_units": [
            {
                "jd_unit_id": capability["job_unit_id"],
                "source_section": "responsibility",
                "capabilities": [capability],
            }
            for capability in capabilities
        ],
        "assessment_units": [],
        "capability_groups": [],
    }
    previous = {
        "application_id": "APP_1",
        "profile_version": "CCP_APP_1_SCREENING_V1",
        "stage": "screening",
        "job_profile": job_profile,
        "pair_assessments": previous_evidence,
        "job_capability_results": previous_results,
        "job_unit_results": [],
        "job_capability_fit_assessments": [],
        "score_summary": {"job_requirement_score": 77.0},
    }

    def fake_assess_current_job_capability(**kwargs):
        subset = kwargs["job_profile"]["job_capabilities"]
        assert [item["job_capability_id"] for item in subset] == ["CAP_1", "CAP_2"]
        return {
            "pair_assessments": previous_evidence,
            "job_capability_results": previous_results,
            "prompt_traces": [],
        }

    monkeypatch.setattr(
        incremental,
        "assess_current_job_capability",
        fake_assess_current_job_capability,
    )

    result = incremental.update_job_capability_with_interview(
        previous_result=previous,
        resume_profile={},
        interview_evidence=[],
        new_profile_version="CCP_APP_1_AFTER_FIRST_V2",
        stage="after_first_interview",
        scoring_evidence={
            "affected_project_ids": ["P_1"],
            "corrected_skill_claim_ids": [],
        },
    )

    assert result["change_summary"]["affected_job_capability_ids"] == ["CAP_1", "CAP_2"]
    assert {
        item["job_capability_id"] for item in result["job_capability_results"]
    } == {"CAP_1", "CAP_2"}
