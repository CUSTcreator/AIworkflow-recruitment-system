from __future__ import annotations

"""综合得分计算。

岗位要求能力、简历经历能力和学历只在此处按固定权重合成；各能力模型内部的
证据聚合不应重复承担总分权重，避免同一证据被跨层重复放大。
"""

from typing import Any


SCORE_ENGINE_VERSION = "candidate_profile_score_engine_v1_1"
DEFAULT_EDUCATION_BACKGROUND_SCORE = 75.0
TOTAL_SCORE_WEIGHTS = {
    "job_requirement": 0.50,
    "resume_experience": 0.35,
    "education": 0.15,
}


def score_candidate_profile(profile: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """从完整能力画像生成可追溯的分项分与综合得分快照。"""
    # 1. 读取已经分别完成的岗位匹配、经历能力和学历评估结果。
    job_result = profile.get("job_result") or {}
    preset_result = profile.get("preset_experience_result") or {}
    # 2. 将岗位单元的 0~1 匹配强度换算为百分制，以便兼容已有结果。
    job_fit_values = [_score_to_points(item.get("score")) for item in job_result.get("job_unit_results", [])]
    demonstrated_values = [
        _score_to_points(item.get("score"))
        for item in preset_result.get("candidate_framework_results", [])
    ]
    # 3. 优先采用岗位算法已聚合的岗位分；旧结果缺少时才对岗位单元取平均。
    documented_job_score = (profile.get("score_summary") or {}).get("job_requirement_score")
    job_score = (
        round(float(documented_job_score), 2)
        if isinstance(documented_job_score, (int, float))
        else _avg(job_fit_values)
    )
    # 4. 优先采用经历算法已聚合的经历分；缺少时才对候选人框架取平均。
    documented_resume_score = (profile.get("score_summary") or {}).get("resume_experience_score")
    demonstrated_score = round(float(documented_resume_score), 2) if isinstance(documented_resume_score, (int, float)) else _avg(demonstrated_values)
    # 5. 硬筛资格结论不参与总分计算，保留独立字段由初筛页面展示。
    qualification_status = "not_scored"
    # 6. 规范学历分的量纲；历史百分比和 0~1 比例都统一为百分制。
    documented_education_score = (profile.get("education_result") or {}).get("score")
    if isinstance(documented_education_score, (int, float)) and documented_education_score <= 1:
        documented_education_score = float(documented_education_score) * 100
    education_score = (
        round(float(documented_education_score), 2)
        if isinstance(documented_education_score, (int, float))
        else DEFAULT_EDUCATION_BACKGROUND_SCORE
    )
    # 7. 按固定权重计算总分：岗位匹配 50%、经历能力 35%、学历背景 15%。
    base_score = round(
        TOTAL_SCORE_WEIGHTS["job_requirement"] * job_score
        + TOTAL_SCORE_WEIGHTS["resume_experience"] * demonstrated_score
        + TOTAL_SCORE_WEIGHTS["education"] * education_score,
        2,
    )
    overall_policy = "job_0.50_resume_0.35_education_0.15_v1_0"
    # 8. 生成可解释的分项贡献，方便页面说明总分来源。
    explanation_detail = comprehensive_score_explanation(
        stage=str(profile.get("stage") or "screening"),
        total_score=base_score,
        job_score=job_score,
        resume_score=demonstrated_score,
        education_score=education_score,
    )
    # 9. 生成固定的引擎输出，既包含显示分数也包含精确的原始综合分。
    output = {
        "score_engine_version": SCORE_ENGINE_VERSION,
        "stage": profile.get("stage"),
        "job_capability_fit_score": job_score,
        "resume_demonstrated_capability_score": demonstrated_score,
        "resume_experience_score": demonstrated_score,
        "preset_experience_score": demonstrated_score,
        "education_background_score": education_score,
        "overall_dimension_policy": overall_policy,
        "score_explanation_detail": explanation_detail,
        "qualification_status": qualification_status,
        "base_score": base_score,
        "score": int(round(base_score)),
    }
    # 10. 记录本次计算引用的画像、岗位单元和学历结果，支持审计回放。
    engine_input = {
        "score_engine_version": SCORE_ENGINE_VERSION,
        "profile_id": profile.get("profile_id"),
        "candidate_framework_ids": [
            item.get("framework_id") for item in preset_result.get("candidate_framework_results", [])
        ],
        "job_unit_ids": [
            item.get("job_unit_id") for item in job_result.get("job_unit_results", [])
        ],
        "education_result": profile.get("education_result"),
        "resume_experience_formula_version": (profile.get("score_summary") or {}).get("resume_experience_formula_version"),
    }
    # 11. 返回审计输入与计算输出；调用方据此创建版本化分数快照。
    return engine_input, output


def score_snapshot_from_engine(
    profile: dict[str, Any],
    engine_output: dict[str, Any],
) -> dict[str, Any]:
    """将计算结果压缩为版本化分数快照，详细证据仍保留在能力画像中。"""
    # 1. 将完整能力画像的计算输出压缩为跨阶段可引用、可版本化的分数快照。
    return {
        "stage": profile.get("stage"),
        "score": engine_output["score"],
        "baseScore": engine_output["base_score"],
        "base_score": engine_output["base_score"],
        "jobCapabilityFitScore": engine_output["job_capability_fit_score"],
        "job_capability_fit_score": engine_output["job_capability_fit_score"],
        "resumeDemonstratedCapabilityScore": engine_output["resume_demonstrated_capability_score"],
        "resume_demonstrated_capability_score": engine_output["resume_demonstrated_capability_score"],
        "resumeExperienceScore": engine_output["resume_experience_score"],
        "resume_experience_score": engine_output["resume_experience_score"],
        "educationBackgroundScore": engine_output["education_background_score"],
        "education_background_score": engine_output["education_background_score"],
        "qualificationStatus": engine_output["qualification_status"],
        "qualification_status": engine_output["qualification_status"],
        "scoreEngineVersion": SCORE_ENGINE_VERSION,
        "score_engine_version": SCORE_ENGINE_VERSION,
        "sourceProfileId": profile.get("profile_id"),
        "candidate_capability_profile_ref": {
            "profile_id": profile.get("profile_id"),
            "stage": profile.get("stage"),
        },
        "scoreExplanation": engine_output.get("score_explanation_detail"),
        "score_explanation_detail": engine_output.get("score_explanation_detail"),
    }

def comprehensive_score_explanation(
    *,
    stage: str,
    total_score: float,
    job_score: float,
    resume_score: float,
    education_score: float,
) -> dict[str, Any]:
    components = [
        _explanation_component("job_requirement", "岗位要求能力", job_score, TOTAL_SCORE_WEIGHTS["job_requirement"]),
        _explanation_component("preset_experience", "预设经历能力", resume_score, TOTAL_SCORE_WEIGHTS["resume_experience"]),
        _explanation_component("education", "学历背景", education_score, TOTAL_SCORE_WEIGHTS["education"]),
    ]
    return {
        "stage": stage,
        "score_name": "候选人能力分",
        "display_score": round(total_score, 2),
        "formula_version": SCORE_ENGINE_VERSION,
        "components": components,
        "notes": [
            "风险项和面试重点不直接加减总分。",
            "硬性筛选结果独立于综合得分。",
        ],
    }


def _explanation_component(key: str, label: str, score: float, weight: float) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "score": round(score, 2),
        "weight": weight,
        "contribution": round(score * weight, 2),
    }


def _score_to_points(score: Any) -> float:
    if not isinstance(score, (int, float)):
        return 0.0
    return round(_clip(float(score), 0, 1) * 100, 2)


def _level_to_points(level: Any) -> float:
    if not isinstance(level, (int, float)):
        return 0.0
    return round(_clip(float(level), 0, 5) * 20, 2)


def _avg(values: list[float]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 2)


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
