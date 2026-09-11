from __future__ import annotations

"""CandidateCapabilityProfile 构建器。

它负责把岗位匹配、预设经历能力、学历和派生展示信号收敛为同一版本画像；
画像是后续面试更新的输入，不是页面临时拼装结果。
"""

from typing import Any

from recruitment_ai_core.common.capability_profile_contracts import PROFILE_SCHEMA_VERSION
from recruitment_ai_core.common.interview_targets import build_interview_targets
from recruitment_ai_core.screening_scoring.resume_experience.preset_models import (
    MODEL_CATALOG_VERSION as CATALOG_VERSION,
    MODEL_POLICY_VERSION as POLICY_VERSION,
)

from .education_scoring import score_education
from .score_engine import TOTAL_SCORE_WEIGHTS, score_candidate_profile
from .strength_signals import build_strength_signals
from .weakness_signals import build_weakness_signals


def build_screening_core_profile(
    *,
    job_profile: dict[str, Any],
    resume_profile: dict[str, Any],
    job_fit_assessments: list[dict[str, Any]],
    qualification: dict[str, Any],
    resume_experience_result: dict[str, Any] | None = None,
    job_capability_result: dict[str, Any] | None = None,

    education_ranking_entries: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    list[dict[str, Any]],
]:
    """构建纯评分核心的能力图谱与分数，不生成规则或展示产物。"""
    # 1. 统一读取经历和岗位计算结果；资格结论已在外层分数与页面结果中单独处理。
    del qualification
    experience_result = resume_experience_result or {}
    capability_result = job_capability_result or {}

    capability_results = capability_result.get("job_capability_results", [])
    pair_assessments = capability_result.get("pair_assessments", [])
    job_unit_results = capability_result.get("job_unit_results", job_fit_assessments)
    candidate_framework_results = experience_result.get("candidate_framework_results", [])
    # 3. 依据简历学历事实和固定院校排名计算学历分及其来源说明。
    education_result = score_education(resume_profile, education_ranking_entries)
    # 6. 组装初筛 V1 能力画像，保存来源版本、各层结果、派生 Signal 和目标 ID。
    profile = {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "job_profile_version_id": job_profile["job_profile_version_id"],
        "resume_profile_version_id": resume_profile["resume_profile_version_id"],
        "previous_profile_id": None,
        "preset_model_id": job_profile["preset_model_id"],
        "preset_model_version": job_profile["preset_model_version"],
        "algorithm_versions": {
            "preset_experience": experience_result.get("algorithm_version", "preset_experience_assessment_v2_0"),
            "job_requirement": capability_result.get("schema_version", "job_requirement_assessment_v1_0"),
            "interview_update": "interview_update_v1_0",
            "education": "education_scoring_v1_0",
            "total": "job_0.50_resume_0.35_education_0.15_v1_0",
        },
        "source_inputs": {
            "resume_profile_version_id": resume_profile["resume_profile_version_id"],
            "job_profile_version_id": job_profile["job_profile_version_id"],
            "interview_parse_result_ids": [],
            "ranking_dataset_version": "softke_bcur_2026",
        },
        "weights": {
            "job_requirement": TOTAL_SCORE_WEIGHTS["job_requirement"],
            "preset_experience": TOTAL_SCORE_WEIGHTS["resume_experience"],
            "education": TOTAL_SCORE_WEIGHTS["education"],
        },
        "preset_experience_result": {
            "project_experience_assessments": experience_result.get(
                "project_experience_assessments", []
            ),
            "work_unit_indicator_results": experience_result.get(
                "work_unit_indicator_results", []
            ),
            "project_pao_results": experience_result.get(
                "project_pao_results", []
            ),
            "project_indicator_results": experience_result.get(
                "project_indicator_results", []
            ),
            "project_framework_results": experience_result.get("project_framework_results", []),
            "observation_target_results": [],
            "interview_round_framework_evidence": [],
            "candidate_framework_results": candidate_framework_results,
            "candidate_framework_updates": [],
        },
        "job_result": {
            "pair_assessments": pair_assessments,
            "capability_results": capability_results,
            "job_unit_results": job_unit_results,
            "observation_target_results": [],
            "interview_round_evidence": [],
        },
        "education_result": education_result,
        "change_summary": {
            "changed_evidence_refs": [],
            "recomputed_result_refs": [
                *[item.get("framework_id") for item in candidate_framework_results if item.get("framework_id")],
                *[item.get("job_unit_id") for item in job_unit_results if item.get("job_unit_id")],
            ],
        },
        "metadata": {
            "capability_indicator_catalog_version": experience_result.get("catalog", {}).get("catalog_version", CATALOG_VERSION),
            "applicability_policy_version": experience_result.get("policy_version", POLICY_VERSION),
            "job_capability_versions": capability_result.get("versions", {}),
            "job_capability_warnings": capability_result.get("warnings", []),
            **(metadata or {}),
        },
    }
    # 7. 临时合并经历与岗位的分数摘要，为统一总分引擎提供输入。
    profile["score_summary"] = {
        **experience_result.get("score_summary", {}),
        **capability_result.get("score_summary", {}),
    }
    # 8. 按固定权重计算岗位匹配、经历、学历和候选人总分。
    score_engine_input, score_engine_output = score_candidate_profile(profile)
    profile["scores"] = {
        "job_requirement": score_engine_output["job_capability_fit_score"] / 100,
        "preset_experience": score_engine_output["preset_experience_score"] / 100,
        "education": score_engine_output["education_background_score"] / 100,
        "total": score_engine_output["base_score"] / 100,
    }
    # 9. 将最终分数写回正式画像，并移除只服务于计算的临时分数摘要。
    profile.pop("score_summary", None)
    return (
        profile,
        score_engine_input,
        score_engine_output,
    )
