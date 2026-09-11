from __future__ import annotations

from recruitment_ai_core.post_second_scoring import run_post_second_scoring


def test_post_second_scoring_uses_free_notes_without_a_question_plan():
    result = run_post_second_scoring(
        {
            "application_id": "APP_TEST",
            "candidate_id": "CAND_TEST",
            "job_id": "JOB_TEST",
            "job_title": "Agent 开发实习生",
            "after_first_score_snapshot": {
                "stage": "after_first_interview",
                "score": 78,
                "baseScore": 78,
                "jobCapabilityFitScore": 77,
                "resumeExperienceScore": 79,
                "educationBackgroundScore": 76,
            },
            "hr_raw_notes": [
                {
                    "rawNotesId": "RAW_HR_001",
                    "content": (
                        "候选人的岗位动机清晰，能明确说明希望从事 Agent 工程方向。\n"
                        "每周可投入四天，到岗时间明确，实习周期稳定。\n"
                        "沟通主动，遇到需求不清楚时会同步目标、边界和依赖。"
                    ),
                }
            ],
            "hr_final_assessment": {
                "assessmentId": "ASSESS_SECOND_APP_TEST",
                "rawNotesId": "RAW_HR_001",
                "summary": "岗位动机、到岗条件和沟通协作均较匹配。",
                "technicalFollowupNeeded": True,
                "technicalFollowupItems": ["评测指标仍需要技术负责人复核。"],
            },
        }
    )

    assert result["status"] == "scored"
    assert result["post_second_bundle"]["bundle_schema_version"] == "post_second_scoring_bundle_v2_0"
    assert result["after_second_score_snapshot"]["stage"] == "after_second_interview"
    assert result["after_second_score_snapshot"]["score"] == round(
        result["after_second_score_snapshot"]["candidate_ability_score"]
    )
    assert result["after_second_score_snapshot"]["baseScore"] == result["after_second_score_snapshot"]["role_capability_score"]
    assert "decision_fit_score" not in result["after_second_score_snapshot"]
    assert result["after_second_score_snapshot"]["score_explanation_detail"]["score_name"] == "候选人能力分"
    assert result["hr_structured_draft"]["interviewRound"] == "second"
    assert result["hr_evidence"]
    assert result["second_interview_evidence_units"]
    assert all(
        unit["source_type"] != "planned_second_question"
        for unit in result["second_interview_evidence_units"]
    )
    package = result["final_candidate_review_package"]
    assert package["hrSecondRoundEvidence"]
    assert "non_scoring" in package
