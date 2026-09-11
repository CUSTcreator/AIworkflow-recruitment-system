from __future__ import annotations

from recruitment_ai_core.screening_scoring.screening_view_builder import (
    build_screening_result_view,
)


def test_candidate_framework_view_reads_explanatory_candidate_indicators() -> None:
    view = build_screening_result_view(
        application_id="APP_1",
        candidate_profile={
            "preset_experience_result": {
                "candidate_framework_results": [{
                    "candidate_framework_result_id": "CDR_solution_execution",
                    "framework_id": "solution_execution",
                    "score": 0.82,
                    "primary_project_framework_result_id": "PDR_1",
                    "supplemental_project_framework_result_ids": [],
                }],
                "project_indicator_results": [{
                    "project_indicator_result_id": "PIR_1",
                    "project_id": "P_1",
                    "indicator_id": "solution_fit_scale",
                    "score": 0.77,
                    "project_evaluation": {
                        "level": 3,
                        "source_refs": [{"work_unit_id": "WU_1"}],
                    },
                }],
            },
            "job_result": {"capability_results": [], "job_unit_results": []},
            "risks": [],
            "interview_targets": [],
        },
        job_profile={"jd_units": [], "assessment_units": []},
        score_output={"base_score": 80},
        qualification={"gate": "pass"},
        resume_profile={"scorable_work_units": [], "source_bullets": []},
    )

    framework = view["resumeCapabilities"][0]
    assert framework["activatedIndicatorCount"] == 1
    assert framework["indicatorCount"] == 5
    assert framework["indicators"][0]["indicatorId"] == "solution_fit_scale"
    assert framework["indicators"][0]["levelDescription"]
