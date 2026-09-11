from __future__ import annotations

"""岗位要求能力公开入口。

先将 JD 固化为可复用的岗位画像，再以运行时评分证据进行匹配；岗位定义不能在
查看某位候选人的简历后被改写。
"""

import hashlib
from typing import Any

from .contracts import JobCapabilityInput
from .current import (
    _aggregate_job,
    _aggregate_job_units,
    assess_current_job_capability,
    compile_current_job_profile,
)


def compile_job_profile(
    job_id: str,
    jd_text: str,
    *,
    frozen_job_json: dict[str, Any] | None = None,
    business_hard_constraints: list[dict[str, Any]] | None = None,
    business_group_weights: dict[str, float] | None = None,
    assessment_units: list[dict[str, Any]] | None = None,
    llm_config: dict[str, Any] | None = None,
    preset_model: dict[str, Any] | None = None,
    extracted_capabilities: dict[str, list[dict[str, Any]]] | None = None,
    extraction_traces: list[dict[str, Any]] | None = None,
    extraction_degraded: bool = False,
    requirement_classification: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """把 JD 编译为 JDUnit 与 JDCapability 构成的岗位画像。"""
    # 1. 委托当前岗位编译器，将 JD 固化为不依赖候选人的版本化岗位画像。
    return compile_current_job_profile(
        job_id,
        jd_text,
        frozen_job_json=frozen_job_json,
        llm_config=llm_config,
        assessment_units=assessment_units,
        preset_model=preset_model,
        extracted_capabilities=extracted_capabilities,
        extraction_traces=extraction_traces,
        extraction_degraded=extraction_degraded,
        requirement_classification=requirement_classification,
    )


def assess_job_capability(
    *,
    application_id: str,
    job_profile: dict[str, Any],
    scoring_evidence: dict[str, Any],
    profile_version: str,
    stage: str = "screening",
    llm_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """以本次调用的运行时评分证据评估候选人对岗位画像的支持程度。"""
    if not scoring_evidence:
        raise ValueError("scoring_evidence_required")
    return assess_current_job_capability(
        application_id=application_id,
        job_profile=job_profile,
        scoring_evidence=scoring_evidence,
        profile_version=profile_version,
        stage=stage,
        llm_config=llm_config,
    )


def reaggregate_job_capability_result(
    *,
    job_profile: dict[str, Any],
    capability_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """按 V1 的岗位聚合规则重算 JDUnit 与岗位总分。

    增量流程在仅重算部分 ``JDCapability`` 后，需要将“新叶子 + 沿用叶子”放回
    同一套 V1 聚合公式。这个函数刻意不调用 LLM、不创建对象；它只复用岗位能力
    算法已经定义的 JDUnit 聚合和岗位总分规则，避免 V2/V3 在别处用平均值另算一套分数。
    """
    capabilities = list(job_profile.get("job_capabilities") or [])
    units = [
        *list(job_profile.get("jd_units") or []),
        *list(job_profile.get("assessment_units") or []),
    ]
    job_unit_results = _aggregate_job_units(units, capabilities, capability_results)
    score = _aggregate_job(job_unit_results)
    return {
        "job_unit_results": job_unit_results,
        "score_summary": {
            "job_requirement_score": round(score * 100, 2),
            "job_capability_fit_score": round(score * 100, 2),
        },
    }


def run_job_capability_pipeline(
    payload: JobCapabilityInput | dict[str, Any],
) -> dict[str, Any]:
    # 1. 规范化岗位匹配输入，算法层只使用固定的字典契约。
    input_data = (
        payload if isinstance(payload, JobCapabilityInput) else JobCapabilityInput(**payload)
    )
    # 2. 校验调用方提供的岗位画像是否与当前岗位和 JD 内容哈希一致。
    job_profile = input_data.job_profile
    expected_sha256 = hashlib.sha256(input_data.jd_text.encode("utf-8")).hexdigest()
    # 3. 岗位画像必须已在岗位发布阶段冻结；候选人评分不得在运行时改写或重编译 JD。
    if (
        not job_profile
        or job_profile.get("job_id") != input_data.job_id
        or (input_data.jd_text and job_profile.get("source_sha256") != expected_sha256)
    ):
        raise ValueError("frozen_job_profile_required")
    # 4. 使用固定岗位画像和本次运行时评分证据，执行岗位能力匹配并输出申请级结果。
    return assess_job_capability(
        application_id=input_data.application_id,
        job_profile=job_profile,
        profile_version=input_data.profile_version,
        stage=input_data.stage,
        scoring_evidence=input_data.scoring_evidence,
        llm_config=input_data.llm_config,
    )
