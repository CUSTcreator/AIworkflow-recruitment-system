from __future__ import annotations

from backend.app.models.entities import Candidate, CandidateProfile


def candidate_profile_view(
    candidate: Candidate | None,
    profile: CandidateProfile | None,
) -> dict:
    """将 Candidate 与其一对一档案投影为旧页面可稳定消费的候选人视图。

    该函数不是新的持久化来源；它只让评估、面试等读模型逐步摆脱
    旧页面字段名，同时保持现有前端字段名不变。
    """
    if candidate is None:
        return {}
    return {
        "candidateId": candidate.candidate_id,
        "displayName": candidate.display_name,
        "anonymizedCode": candidate.candidate_id.replace("CAND_", "C-"),
        "currentTitle": profile.current_title if profile is not None else "",
        "yearsOfExperience": profile.years_of_experience if profile is not None else "",
        "education": profile.education if profile is not None else "",
        "age": profile.age if profile is not None else None,
        "school": profile.school if profile is not None else "",
        "major": profile.major if profile is not None else "",
        "phone": profile.phone if profile is not None else "",
        "email": profile.email if profile is not None else "",
        "highestDegree": profile.highest_degree if profile is not None else "",
        "graduationYear": profile.graduation_year if profile is not None else None,
    }
