from recruitment_ai_core.screening_scoring.resume_experience.aggregation import (
    aggregate_project_frameworks,
)
from recruitment_ai_core.screening_scoring.resume_experience.model_selector import (
    recommend_preset_model,
    resolve_preset_model,
)
from recruitment_ai_core.screening_scoring.resume_experience.preset_models import (
    get_preset_model,
)


def test_recommendation_separates_engineering_and_general_roles() -> None:
    assert recommend_preset_model("软件开发工程师", "负责系统设计和代码开发")[
        "preset_model_id"
    ] == "engineering_experience"
    assert recommend_preset_model("采购专员", "负责供应商管理、询价和合同执行")[
        "preset_model_id"
    ] == "general_professional_experience"


def test_confirmed_job_model_has_priority_over_recommendation() -> None:
    model = resolve_preset_model(
        job_profile=None,
        job_metadata={
            "preset_model_id": "general_professional_experience",
            "preset_model_version": "1.0",
        },
        job_title="软件开发工程师",
        jd_text="负责系统开发",
    )
    assert model["model_id"] == "general_professional_experience"


def test_general_model_uses_its_own_fixed_framework_indicator_count() -> None:
    model = get_preset_model("general_professional_experience")
    results = aggregate_project_frameworks(
        "P_1",
        [{
            "indicator_id": "approach_fit",
            "score": 0.77,
        }],
        model,
    )
    solution = next(
        item for item in results if item["framework_id"] == "solution_execution"
    )
    assert len(next(
        item["indicator_ids"] for item in model["frameworks"]
        if item["framework_id"] == "solution_execution"
    )) == 4
    assert solution["active_indicator_ids"] == ["approach_fit"]
