"""岗位画像的 JDUnit 活动级恢复服务。

岗位画像的领域发布仍由 JobProfileService 完成。本服务只负责把同一冻结 JD 中彼此独立
的能力提取请求交给 ActivityRunner；任一单元恢复时不会重复调用已成功单元。
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from backend.app.infrastructure.workflow_runtime import (
    ActivityBatchResult,
    ActivityDefinition,
    ActivityPolicy,
    ActivityResolution,
    ActivityRunner,
    is_safe_model_degradation_error,
)
from backend.app.modules.jobs.services.job_profile_service import JobProfileCompilationInput
from backend.app.modules.jobs.profile_readiness import evaluate_job_profile_json
from backend.app.shared.workflows import ActivityExhaustionPolicy, ActivityOutcomeKind, StepContext
from recruitment_ai_core.job_capability.current import (
    extract_job_unit_capabilities_batch,
    _fallback_capabilities,
    plan_job_unit_batches,
    prepare_job_profile_units,
)
from recruitment_ai_core.job_capability.pipeline import compile_job_profile
from recruitment_ai_core.job_capability.requirement_classification import (
    deterministic_requirement_fallback,
)
from recruitment_ai_core.screening_scoring.resume_experience.preset_models import get_preset_model


class JobProfileActivityService:
    """将 JDUnit 的 LLM 提取和纯画像汇总分离。"""

    def __init__(self, runner: ActivityRunner | None = None) -> None:
        self.runner = runner or ActivityRunner()

    def compile(self, context: StepContext, frozen: JobProfileCompilationInput) -> ActivityBatchResult:
        """按预算批次恢复 JDUnit 能力提取，再使用全部活动产物纯汇总岗位画像。"""
        preset_model = get_preset_model(frozen.preset_model_id, frozen.preset_model_version)

        # 硬筛分类已在岗位确认前完成并冻结；画像阶段只复用该结果，避免重复分类或
        # 后台生成第二套未经过用户确认的硬筛策略。历史版本缺少该字段时才使用
        # 确定性规则作为保守回退，仍不会在此阶段调用要求分类 LLM。
        requirement_classification = dict(
            frozen.frozen_job_json.get("requirement_classification") or {}
        )
        if not requirement_classification:
            requirement_classification = deterministic_requirement_fallback(
                frozen.frozen_job_json
            )
        units = prepare_job_profile_units(
            frozen.job_id,
            frozen.source_text,
            frozen_job_json=frozen.frozen_job_json,
            requirement_classification=requirement_classification,
        )
        if not units:
            return ActivityBatchResult(
                status="blocked",
                results={},
                error_code="job_profile_source_units_missing",
                error_message="岗位要求中缺少可用于生成能力画像的职责或任职资格",
            )
        # 预算规划只在 JDUnit 边界打包；同一批失败时仍能按批次检查点恢复。
        # 单个 JDUnit 超预算时不强行发送必然失败的请求，保留原子边界并本地降级。
        try:
            unit_batches = plan_job_unit_batches(units, preset_model=preset_model)
        except ValueError as error:
            error_code = str(error)
            if not error_code.startswith("llm_budget_"):
                raise
            fallback = _fallback_capabilities(units)
            profile = compile_job_profile(
                frozen.job_id,
                frozen.source_text,
                frozen_job_json=frozen.frozen_job_json,
                preset_model=preset_model,
                extracted_capabilities=fallback,
                extraction_traces=[{
                    "stage": "jd_capability_budget_plan",
                    "outcome": "degraded",
                    "resolution_code": "jd_unit_budget_local_fallback",
                    "error_code": error_code,
                    "job_unit_ids": [str(unit["job_unit_id"]) for unit in units],
                }],
                extraction_degraded=True,
                requirement_classification=requirement_classification,
            )
            return self._profile_result(
                profile,
                status=ActivityOutcomeKind.DEGRADED.value,
                outcome_kinds={
                    "jd_capability_budget_plan": ActivityOutcomeKind.DEGRADED.value,
                },
                quality_summaries={
                    "jd_capability_budget_plan": {
                        "usable": True,
                        "degraded": True,
                        "resolutionCode": "jd_unit_budget_local_fallback",
                        "errorCode": error_code,
                        "jobUnitIds": [str(unit["job_unit_id"]) for unit in units],
                    }
                },
            )
        activities = [
            ActivityDefinition(
                activity_key=f"jd_capability:batch:{int(batch['batchIndex']):03d}",
                input_data={
                    "jdVersionId": frozen.jd_version_id,
                    "sourceSha256": frozen.source_sha256,
                    "units": list(batch["units"]),
                    "jobTitle": str(frozen.frozen_job_json.get("title") or ""),
                    "estimatedInputTokens": batch["estimatedInputTokens"],
                    "estimatedOutputTokens": batch["estimatedOutputTokens"],
                    "presetModelId": frozen.preset_model_id,
                    "presetModelVersion": frozen.preset_model_version,
                },
                handler=lambda _activity, batch=batch: extract_job_unit_capabilities_batch(
                    frozen.job_id,
                    list(batch["units"]),
                    job_title=str(frozen.frozen_job_json.get("title") or ""),
                    preset_model=preset_model,
                ),
                policy=ActivityPolicy(max_attempts=2, retry_after_seconds=15),
                exhaustion_policy=ActivityExhaustionPolicy.FALLBACK_TO_STANDARD_MODEL,
                on_exhausted=lambda _activity, error, batch=batch: self._local_fallback_resolution(
                    batch["units"], error
                ),
                can_degrade=is_safe_model_degradation_error,
            )
            for batch in unit_batches
        ]
        batch = self.runner.run_many(
            workflow_run_id=context.workflow_run_id,
            worker_id=context.worker_id,
            parent_step_name=context.step_name,
            activities=activities,
            parallel=True,
        )
        if not batch.is_usable:
            return batch

        extracted: dict[str, list[dict[str, Any]]] = {}
        traces: list[dict[str, Any]] = []
        for definition in activities:
            item = batch.results[definition.activity_key]
            for unit_result in item.get("items") or []:
                extracted[str(unit_result["jobUnitId"])] = list(unit_result.get("capabilities") or [])
            traces.extend(list(item.get("traces") or []))
        profile = compile_job_profile(
            frozen.job_id,
            frozen.source_text,
            frozen_job_json=frozen.frozen_job_json,
            preset_model=preset_model,
            extracted_capabilities=extracted,
            extraction_traces=traces,
            extraction_degraded=batch.status == ActivityOutcomeKind.DEGRADED.value,
            requirement_classification=requirement_classification,
        )
        return self._profile_result(
            profile,
            status=batch.status,
            outcome_kinds=dict(batch.outcome_kinds),
            quality_summaries=dict(batch.quality_summaries),
        )

    @staticmethod
    def _local_fallback_resolution(
        units: list[dict[str, Any]], error: Exception,
    ) -> ActivityResolution:
        """保留模型故障轨迹，并按已冻结 JDUnit 生成可继续发布的保守画像。"""
        fallback = _fallback_capabilities(units)
        job_unit_ids = [str(unit["job_unit_id"]) for unit in units]
        return ActivityResolution(
            outcome_kind=ActivityOutcomeKind.DEGRADED,
            payload={
                "items": [{
                    "jobUnitId": unit_id,
                    "capabilities": fallback.get(unit_id, []),
                    "degraded": True,
                } for unit_id in job_unit_ids],
                "traces": [{
                    "outcome": "degraded",
                    "resolution_code": "jd_unit_batch_local_fallback",
                    "error_type": type(error).__name__,
                    "error": str(error)[:240],
                }],
            },
            resolution_code="jd_unit_batch_local_fallback",
            quality_summary={
                "usable": True,
                "degraded": True,
                "jobUnitIds": job_unit_ids,
                "errorType": type(error).__name__,
            },
        )

    @staticmethod
    def _profile_result(
        profile: dict[str, Any],
        *,
        status: str,
        outcome_kinds: dict[str, str],
        quality_summaries: dict[str, dict[str, Any]],
    ) -> ActivityBatchResult:
        """校验最终画像覆盖率，并保留活动或本地降级的质量记录。"""
        readiness = evaluate_job_profile_json(profile)
        if not readiness.ready:
            return ActivityBatchResult(
                status="blocked",
                results={},
                outcome_kinds=outcome_kinds,
                quality_summaries=quality_summaries,
                error_code="job_profile_minimum_coverage_missing",
                error_message=(
                    "岗位能力提取未覆盖全部职责单元，不能发布空或不完整的岗位画像；"
                    f"缺少单元：{', '.join(readiness.missing_required_unit_ids) or '全部'}"
                ),
            )
        return ActivityBatchResult(
            status=status,
            results={"job_profile": profile},
            outcome_kinds=outcome_kinds,
            quality_summaries=quality_summaries,
        )
