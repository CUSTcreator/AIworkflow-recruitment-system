"""评分来源运行时投影。

持久化画像中的 ``profile_json`` 只保存算法业务事实；画像主键、JD 版本和预设模型
属于关系型具名列。纯算法需要两类信息时，必须通过本文件在冻结边界显式合成运行时
字典，禁止把身份字段重新写回 JSON，或回退到任何泛化 payload。
"""
from __future__ import annotations

from typing import Any

from backend.app.models.entities import JobRequirementProfileRecord, JobVersionRecord, ResumeProfileRecord


def project_scoring_resume_profile(profile: ResumeProfileRecord) -> dict[str, Any]:
    """将已冻结的简历画像投影为评分算法合同，而不修改持久化 JSON。"""
    value = dict(profile.profile_json or {})
    stored_id = str(value.get("resume_profile_version_id") or "")
    if stored_id and stored_id != profile.resume_profile_id:
        raise RuntimeError("frozen_resume_profile_identity_mismatch")
    return {
        **value,
        # 两个名称分别兼容评分核心和增量评分来源；值都精确指向同一不可变记录。
        "resume_profile_version_id": profile.resume_profile_id,
        "resume_profile_id": profile.resume_profile_id,
    }


def project_scoring_job_profile(
    profile: JobRequirementProfileRecord,
    job_version: JobVersionRecord,
) -> dict[str, Any]:
    """将岗位画像和它冻结的 JD 版本合成为评分算法合同。"""
    if profile.job_id != job_version.job_id or profile.jd_version_id != job_version.jd_version_id:
        raise RuntimeError("frozen_job_profile_version_mismatch")
    value = dict(profile.profile_json or {})
    stored_id = str(value.get("job_profile_version_id") or "")
    if stored_id and stored_id != profile.job_profile_id:
        raise RuntimeError("frozen_job_profile_identity_mismatch")
    return {
        **value,
        "job_profile_version_id": profile.job_profile_id,
        "job_profile_id": profile.job_profile_id,
        "job_id": profile.job_id,
        "jd_version_id": profile.jd_version_id,
        "preset_model_id": job_version.preset_model_id,
        "preset_model_version": job_version.preset_model_version,
        "algorithm_version": profile.algorithm_version,
    }