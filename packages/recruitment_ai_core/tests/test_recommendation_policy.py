from recruitment_ai_core.decision_summary.fallback import build_summary_text
from recruitment_ai_core.decision_summary.recommendation_policy import (
    POLICY_VERSION,
    compute_recommendation_level,
    recommendation_display_text,
)


def test_recommendation_policy_uses_key_dimensions_and_never_neutral() -> None:
    assert POLICY_VERSION == "recommendation_policy_v1"
    assert compute_recommendation_level({"total": 80.03, "job_fit": 70.55, "experience": 88.45}) == "recommend"
    assert compute_recommendation_level({"total": 90, "job_fit": 90, "experience": 90}, has_critical_gap=True) == "cautious_recommend"
    assert compute_recommendation_level({"total": 30, "job_fit": 20, "experience": 70}) == "strongly_not_recommend"
    assert compute_recommendation_level({"total": 70, "job_fit": 60, "experience": 70}) in {
        "recommend", "cautious_recommend"
    }


def test_recommendation_display_text_is_stage_specific() -> None:
    assert recommendation_display_text("cautious_recommend", "screening") == "建议通过初筛，需重点核验"
    assert recommendation_display_text("recommend", "after_first_interview") == "建议进入二面"
    assert recommendation_display_text("strongly_not_recommend", "after_second_interview") == "明确不录用"


def test_summary_text_is_specific_and_at_most_100_characters() -> None:
    bundle_input = {
        "strengths": [{"target_name": "结构设计", "result_summary": "结构设计和计算分析项目经验能够支撑岗位主要职责。"}],
        "weaknesses": [{"target_name": "施工服务", "result_summary": "简历缺少施工技术服务的直接参与证据。"}],
        "verification_focus": [{"verification_goal": "确认实际参与深度和具体产出。"}],
    }
    text = build_summary_text(bundle_input, {
        "strength_sentence": "结构设计和计算分析项目经验能够支撑岗位主要职责。",
        "risk_sentence": "施工技术服务缺少直接参与证据。",
        "action_sentence": "一面确认实际参与深度。",
    })
    assert len(text) <= 100
    assert "结构设计" in text
    assert "施工技术服务" in text
