from recruitment_ai_core.screening_scoring.strength_signals import (
    build_strength_signals,
)


def test_strength_signals_balance_two_model_sources() -> None:
    strengths = build_strength_signals(
        project_indicator_results=[
            {
                "project_indicator_result_id": "PIR_1",
                "project_id": "P_1",
                "indicator_id": "approach_fit",
                "score": 0.80,
                "level": 4,
            },
            {
                "project_indicator_result_id": "PIR_2",
                "project_id": "P_2",
                "indicator_id": "approach_fit",
                "score": 0.60,
                "level": 3,
            },
            {
                "project_indicator_result_id": "PIR_3",
                "project_id": "P_1",
                "indicator_id": "goal_closure",
                "score": 0.76,
                "level": 2,
            },
        ],
        work_unit_indicator_results=[],
        job_capability_results=[{
            "job_capability_result_id": "JCR_1",
            "job_capability_id": "JDC_1",
            "score": 0.87,
            "primary_pair_id": "PAIR_1",
        }],
        pair_assessments=[{
            "pair_id": "PAIR_1",
            "content_level": 4,
            "pair_score": 0.87,
            "proof_work_unit_ids": ["WU_1"],
        }],
        job_capabilities=[{
            "job_capability_id": "JDC_1",
            "capability_name": "AI应用工程化",
            "role": "core",
        }],
    )

    assert [item["source_type"] for item in strengths] == [
        "job_capability",
        "preset_indicator",
    ]
    preset = next(
        item for item in strengths
        if item["source_type"] == "preset_indicator"
    )
    assert preset["proof_scope"] == "aggregate"
    assert preset["proof_pattern"] == "single_l4"
    assert preset["source_result_ids"] == ["PIR_1"]
    assert all("strength_signal_id" in item for item in strengths)


def test_strength_signals_do_not_fill_with_sub_l3_results() -> None:
    strengths = build_strength_signals(
        project_indicator_results=[{
            "project_indicator_result_id": "PIR_LOW",
            "project_id": "P_1",
            "indicator_id": "goal_closure",
            "score": 0.60,
            "level": 2,
        }],
        work_unit_indicator_results=[],
        job_capability_results=[],
        pair_assessments=[],
        job_capabilities=[],
    )

    assert strengths == []
