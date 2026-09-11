"""V1/V2/V3 共享规则入口的回归测试。"""
from __future__ import annotations

from recruitment_ai_core.assessment_rules import derive_assessment_rules


def _core() -> dict:
    return {
        "experience_result": {
            "project_indicator_results": [
                {
                    "project_indicator_result_id": "PIR_HIGH",
                    "project_id": "P1",
                    "indicator_id": "systems_thinking",
                    "level": 4,
                    "score": 0.9,
                    "project_evaluation": {"reason": "主导系统方案拆解", "work_unit_ids": ["WU_1"]},
                },
                {
                    "project_indicator_result_id": "PIR_LOW",
                    "project_id": "P2",
                    "indicator_id": "delivery_closure",
                    "level": 1,
                    "score": 0.2,
                    "project_evaluation": {"reason": "结果闭环事实不足", "work_unit_ids": ["WU_2"]},
                },
            ],
            "work_unit_indicator_results": [],
        },
        "job_result": {"capability_results": [], "pair_assessments": []},
        "score_result": {"total": 82, "job_fit": 80, "experience": 75},
    }


def test_shared_rules_use_evidence_conditions_not_top_and_bottom_scores() -> None:
    result = derive_assessment_rules(
        stage="screening",
        core_result=_core(),
        job_profile={"job_capabilities": []},
        application_id="APP_001",
    )
    assert [item["target_id"] for item in result["strength_signals"]] == ["systems_thinking"]
    assert [item["target_id"] for item in result["weakness_signals"]] == ["delivery_closure"]
    assert result["strength_signals"][0]["signal_key"] == "strength:preset_indicator:systems_thinking"


def test_signal_comparison_keeps_stable_key_across_rounds() -> None:
    first = derive_assessment_rules(
        stage="screening", core_result=_core(), job_profile={"job_capabilities": []}, application_id="APP_001"
    )
    second = derive_assessment_rules(
        stage="after_first_interview",
        core_result=_core(),
        job_profile={"job_capabilities": []},
        application_id="APP_001",
        previous_rule_result={"assessment_version_id": "ASV_V1", "strength_signals": first["strength_signals"], "weakness_signals": first["weakness_signals"]},
    )
    assert second["signal_comparison"]["strengths"][0]["change_status"] == "retained"
    assert second["signal_comparison"]["weaknesses"][0]["change_status"] == "retained"

def test_interview_targets_are_created_only_in_v1_and_not_copied_in_v2() -> None:
    """V2 即使发现新的候选核验项，也只能更新 V1 已创建的 Target。"""
    core = _core()
    core["job_result"] = {
        "capability_results": [{
            "job_capability_result_id": "JCR_NEW",
            "job_capability_id": "JDC_NEW",
            "primary_pair_id": "PAIR_NEW",
        }],
        "pair_assessments": [{
            "pair_id": "PAIR_NEW", "content_level": 1, "proof_work_unit_ids": ["WU_NEW"],
        }],
    }
    job_profile = {"job_capabilities": [{
        "job_capability_id": "JDC_NEW", "capability_name": "新发现的岗位能力", "role": "core",
    }]}
    v1 = derive_assessment_rules(
        stage="screening", core_result=core, job_profile=job_profile, application_id="APP_001"
    )
    assert any(item["target_id"] == "JDC_NEW" for item in v1["target_updates"])

    v2 = derive_assessment_rules(
        stage="after_first_interview",
        core_result=core,
        job_profile=job_profile,
        application_id="APP_001",
        open_targets=[{
            "interview_target_id": "IT_V1_ONLY",
            "purpose": "verify_experience",
            "target_type": "job_capability",
            "target_id": "JDC_V1",
            "title": "V1 已创建能力",
            "stage_created": "screening",
            "status": "open",
        }],
    )
    assert [item["interview_target_id"] for item in v2["target_updates"]] == ["IT_V1_ONLY"]
    # V2 发现新的候选项、原 Target 未进入本轮排名，都不能被解释为“已核验完成”。
    assert v2["target_updates"][0]["status"] == "open"


def test_post_interview_target_closes_only_explicit_stable_id() -> None:
    """同一底层能力可对应多个核验事项；只能关闭面评解析明确返回的那一个。"""
    targets = [
        {
            "interview_target_id": "IT_EXPERIENCE_SCOPE",
            "purpose": "verify_experience",
            "target_type": "job_capability",
            "target_id": "JDC_SYSTEM",
            "title": "核验职责范围",
            "stage_created": "screening",
            "status": "open",
        },
        {
            "interview_target_id": "IT_DECISION_DEPTH",
            "purpose": "verify_decision",
            "target_type": "job_capability",
            "target_id": "JDC_SYSTEM",
            "title": "核验方案取舍",
            "stage_created": "screening",
            "status": "open",
        },
    ]
    result = derive_assessment_rules(
        stage="after_first_interview",
        core_result=_core(),
        job_profile={"job_capabilities": []},
        application_id="APP_001",
        open_targets=targets,
        resolved_interview_target_ids={"IT_EXPERIENCE_SCOPE"},
    )
    status_by_id = {item["interview_target_id"]: item["status"] for item in result["target_updates"]}
    assert status_by_id == {
        "IT_EXPERIENCE_SCOPE": "resolved",
        "IT_DECISION_DEPTH": "open",
    }
