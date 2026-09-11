from __future__ import annotations

"""初筛纯评分核心。

这里仅负责算法事实的计算，不认识数据库或业务身份。为了支持工作流活动级恢复，
经历评分、岗位能力评分和最终汇总分别暴露为纯函数；兼容入口仍按原顺序组合它们。
"""

from typing import Any

from recruitment_ai_core.common.scoring_evidence import build_scoring_evidence
from recruitment_ai_core.job_capability import assess_job_capability

from .candidate_profile_builder import build_screening_core_profile
from .result_contracts import ScoringCoreInput, ScreeningCoreResult
from .resume_experience import assess_resume_experience
from .resume_experience.preset_models import get_preset_model
from .screening_view_builder import build_evidence_index


def score_resume_evidence(input_data: ScoringCoreInput) -> dict[str, Any]:
    """冻结简历与岗位模型 → 候选人经历能力结果。

    该函数可由每段经历活动的纯汇总结果调用；兼容场景下也可直接运行完整经历评分。
    """
    resume_profile, job_profile = _validate_input(input_data)
    model = get_preset_model(
        str(job_profile.get("preset_model_id") or ""),
        str(job_profile.get("preset_model_version") or ""),
    )
    return assess_resume_experience(
        resume_profile,
        llm_config=dict(input_data.llm_config or {}),
        preset_model=model,
    )


def score_job_capabilities(
    input_data: ScoringCoreInput,
    resume_experience_result: dict[str, Any],
) -> dict[str, Any]:
    """经历能力结果 → 岗位能力匹配结果；不产生正式领域对象。"""
    resume_profile, job_profile = _validate_input(input_data)
    scoring_evidence = build_scoring_evidence(
        resume_profile=resume_profile,
        resume_experience_result=resume_experience_result,
        preset_model_id=str(job_profile["preset_model_id"]),
        preset_model_version=str(job_profile["preset_model_version"]),
    )
    return assess_job_capability(
        application_id="screening_core_scope",
        job_profile=job_profile,
        scoring_evidence=scoring_evidence,
        profile_version="screening_core_v1",
        stage="screening",
        llm_config=dict(input_data.llm_config or {}),
    )


def assemble_screening_core(
    input_data: ScoringCoreInput,
    resume_experience_result: dict[str, Any],
    job_capability_result: dict[str, Any],
) -> ScreeningCoreResult:
    """汇总已完成的活动产物为 ``ScreeningCoreResult``，不触发外部调用。"""
    resume_profile, _job_profile = _validate_input(input_data)
    core_profile, _score_input, score_result = build_screening_core_profile(
        job_profile=dict(job_capability_result["job_profile"]),
        resume_profile=resume_profile,
        job_fit_assessments=list(job_capability_result.get("job_unit_results", [])),
        qualification={},
        resume_experience_result=resume_experience_result,
        job_capability_result=job_capability_result,
        education_ranking_entries=list(input_data.education_ranking_entries or []),
        metadata={
            "source": "screening_core_v1",
            "ranking_dataset_version": input_data.ranking_dataset_version,
            "resume_experience_llm_audit": resume_experience_result.get("llm_audit", {}),
        },
    )
    core_profile.setdefault("source_inputs", {})["ranking_dataset_version"] = input_data.ranking_dataset_version
    return ScreeningCoreResult(
        resume_profile=resume_profile,
        job_profile=dict(job_capability_result["job_profile"]),
        preset_experience_result=core_profile.get("preset_experience_result", {}),
        job_result=core_profile.get("job_result", {}),
        education_result=core_profile.get("education_result", {}),
        score_result=score_result,
        evidence_index=build_evidence_index(core_profile, resume_profile),
        capability_graph=core_profile,
    )


def run_screening_scoring(input_data: ScoringCoreInput) -> ScreeningCoreResult:
    """兼容入口：依次运行经历评分、岗位评分与纯汇总。"""
    experience = score_resume_evidence(input_data)
    job_result = score_job_capabilities(input_data, experience)
    return assemble_screening_core(input_data, experience, job_result)


def _validate_input(input_data: ScoringCoreInput) -> tuple[dict[str, Any], dict[str, Any]]:
    """校验冻结版本，拒绝隐式回退到“最新”简历或岗位画像。"""
    resume_profile = dict(input_data.resume_profile or {})
    job_profile = dict(input_data.job_profile or {})
    if not resume_profile.get("resume_profile_version_id"):
        raise ValueError("frozen_resume_profile_required")
    if not job_profile.get("job_profile_version_id"):
        raise ValueError("frozen_job_profile_required")
    if not input_data.ranking_dataset_version:
        raise ValueError("ranking_dataset_version_required")
    return resume_profile, job_profile
