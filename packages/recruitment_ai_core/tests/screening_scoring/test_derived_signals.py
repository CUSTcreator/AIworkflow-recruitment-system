from recruitment_ai_core.screening_scoring.strength_signals import (
    build_strength_signals,
)
from recruitment_ai_core.screening_scoring.weakness_signals import (
    build_weakness_signals,
)


def test_project_strength_takes_precedence_over_local_strength() -> None:
    strengths = build_strength_signals(
        project_indicator_results=[
            {
                "project_indicator_result_id": "PIR_1",
                "project_id": "P1",
                "indicator_id": "approach_fit",
                "level": 4,
                "score": 0.87,
            }
        ],
        work_unit_indicator_results=[
            {
                "work_unit_indicator_result_id": "WUIR_1",
                "project_id": "P1",
                "work_unit_id": "WU_1",
                "indicator_id": "approach_fit",
                "level": 5,
                "score": 1.0,
            }
        ],
        job_capability_results=[],
        pair_assessments=[],
        job_capabilities=[],
    )
    assert strengths == [
        {
            "strength_signal_id": "STRENGTH_01",
            "source_type": "preset_indicator",
            "target_id": "approach_fit",
            "proof_scope": "aggregate",
            "proof_pattern": "single_l4",
            "source_result_ids": ["PIR_1"],
        }
    ]


def test_local_repeated_l3_requires_three_independent_work_units() -> None:
    work_units = [
        {
            "work_unit_indicator_result_id": f"WUIR_{index}",
            "project_id": "P1",
            "work_unit_id": f"WU_{index}",
            "indicator_id": "execution_coordination",
            "level": 3,
            "score": 0.77,
        }
        for index in range(1, 4)
    ]
    strengths = build_strength_signals(
        project_indicator_results=[],
        work_unit_indicator_results=work_units,
        job_capability_results=[],
        pair_assessments=[],
        job_capabilities=[],
    )
    assert strengths[0]["proof_scope"] == "local"
    assert strengths[0]["proof_pattern"] == "repeated_l3"
    assert len(strengths[0]["source_result_ids"]) == 3


def test_job_strength_uses_independent_pair_proofs() -> None:
    strengths = build_strength_signals(
        project_indicator_results=[],
        work_unit_indicator_results=[],
        job_capability_results=[
            {
                "job_capability_result_id": "JCR_1",
                "job_capability_id": "JDC_1",
                "score": 0.9,
                "primary_pair_id": "PAIR_PROJECT",
                "supplemental_pair_ids": ["PAIR_UNIT", "PAIR_OTHER"],
            }
        ],
        pair_assessments=[
            {
                "pair_id": "PAIR_PROJECT",
                "content_level": 4,
                "pair_score": 0.87,
                "evidence_type": "project",
                "evidence_id": "P1",
                "proof_work_unit_ids": ["WU_1"],
            },
            {
                "pair_id": "PAIR_UNIT",
                "content_level": 4,
                "pair_score": 0.87,
                "evidence_type": "work_unit",
                "evidence_id": "WU_1",
                "proof_work_unit_ids": ["WU_1"],
            },
            {
                "pair_id": "PAIR_OTHER",
                "content_level": 3,
                "pair_score": 0.77,
                "evidence_type": "work_unit",
                "evidence_id": "WU_2",
                "proof_work_unit_ids": ["WU_2"],
            },
        ],
        job_capabilities=[
            {"job_capability_id": "JDC_1", "role": "core"}
        ],
    )
    assert strengths[0]["proof_pattern"] == "single_l4"
    assert strengths[0]["source_result_ids"] == ["JCR_1"]


def test_weakness_only_uses_l1_aggregate_results() -> None:
    weaknesses = build_weakness_signals(
        project_indicator_results=[
            {
                "project_indicator_result_id": "PIR_L1",
                "project_id": "P1",
                "indicator_id": "goal_closure",
                "level": 1,
                "score": 0.35,
            },
            {
                "project_indicator_result_id": "PIR_L2",
                "project_id": "P2",
                "indicator_id": "result_accuracy_validation",
                "level": 2,
                "score": 0.6,
            },
        ],
        job_capability_results=[
            {
                "job_capability_result_id": "JCR_L1",
                "job_capability_id": "JDC_CORE",
                "score": 0.35,
                "primary_pair_id": "PAIR_L1",
                "supplemental_pair_ids": [],
            },
            {
                "job_capability_result_id": "JCR_L0",
                "job_capability_id": "JDC_EMPTY",
                "score": 0.0,
                "primary_pair_id": None,
                "supplemental_pair_ids": [],
            },
        ],
        pair_assessments=[
            {
                "pair_id": "PAIR_L1",
                "content_level": 1,
                "pair_score": 0.35,
                "evidence_type": "work_unit",
                "evidence_id": "WU_1",
                "proof_work_unit_ids": ["WU_1"],
            }
        ],
        job_capabilities=[
            {"job_capability_id": "JDC_CORE", "role": "core"},
            {"job_capability_id": "JDC_EMPTY", "role": "core"},
        ],
    )
    assert [
        (item["source_type"], item["target_id"])
        for item in weaknesses
    ] == [
        ("job_capability", "JDC_CORE"),
        ("preset_indicator", "goal_closure"),
    ]

