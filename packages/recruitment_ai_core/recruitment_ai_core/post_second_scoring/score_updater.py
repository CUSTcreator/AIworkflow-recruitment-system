from __future__ import annotations

from typing import Any

from recruitment_ai_core.screening_scoring.score_engine import (
    comprehensive_score_explanation,
)

from .contracts import PostSecondScoringInput


def build_score_snapshot(
    input_data: PostSecondScoringInput,
    final_risk_updates: list[dict[str, Any]],
    profile_score_output: dict[str, Any],
) -> dict[str, Any]:
    """Build V3 candidate ability score; non-ability information never enters it."""
    ability_score = round(float(profile_score_output["base_score"]), 2)
    job_score = round(float(profile_score_output["job_capability_fit_score"]), 2)
    resume_score = round(
        float(
            profile_score_output.get(
                "resume_demonstrated_capability_score",
                profile_score_output.get("resume_experience_score", 0),
            )
        ),
        2,
    )
    education_score = round(
        float(profile_score_output.get("education_background_score", 0)), 2
    )
    detail = comprehensive_score_explanation(
        stage="after_second_interview",
        total_score=ability_score,
        job_score=job_score,
        resume_score=resume_score,
        education_score=education_score,
    )
    return {
        "score_snapshot_version": "after_second_score_snapshot_v2_0",
        "stage": "after_second_interview",
        "score": int(round(ability_score)),
        "baseScore": ability_score,
        "base_score": ability_score,
        "candidate_ability_score": ability_score,
        "capability_total_score": ability_score,
        "role_capability_score": ability_score,
        "jobCapabilityFitScore": job_score,
        "job_capability_fit_score": job_score,
        "resumeDemonstratedCapabilityScore": resume_score,
        "resume_demonstrated_capability_score": resume_score,
        "resumeExperienceScore": resume_score,
        "resume_experience_score": resume_score,
        "educationBackgroundScore": education_score,
        "education_background_score": education_score,
        "score_explanation": (
            f"候选人能力分 {ability_score:.1f}：岗位要求能力50%＋预设经历能力35%＋学历背景15%。"
        ),
        "scoreExplanation": detail,
        "score_explanation_detail": detail,
        "summary": f"二面后候选人能力分为 {ability_score:.1f}；非能力信息不参与计算。",
    }
