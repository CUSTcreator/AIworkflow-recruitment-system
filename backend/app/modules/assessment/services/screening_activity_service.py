"""初筛核心的活动级恢复编排。

本服务只把纯算法函数映射为可恢复活动：每段简历经历独立保存成功产物，岗位能力
评分独立保存。它不发布 ApplicationAssessmentVersion，也不修改 Application 状态。
"""
from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
from typing import Any

from backend.app.infrastructure.workflow_runtime import (
    ActivityBatchResult,
    ActivityDefinition,
    ActivityPolicy,
    ActivityResolution,
    ActivityRunner,
    is_safe_model_degradation_error,
)
from backend.app.shared.workflows import (
    ActivityExhaustionPolicy,
    ActivityOutcomeKind,
    StepContext,
)
from backend.app.modules.assessment.services.presentation_generation_service import (
    PresentationGenerationService,
)
from recruitment_ai_core.decision_summary.assessment_presentation_input import build_assessment_presentation_inputs
from recruitment_ai_core.job_capability.current import (
    assemble_job_capability_pair_activities,
    fallback_job_capability_pair_activity,
    prepare_job_capability_pair_activities,
    score_job_capability_pair_activity,
)
from recruitment_ai_core.screening_scoring.result_contracts import (
    PresentationResult,
    RuleDerivedResult,
    ScoringCoreInput,
)
from recruitment_ai_core.screening_scoring.resume_experience import (
    assemble_resume_experience,
    assess_resume_project,
    build_unassessed_resume_project,
)
from recruitment_ai_core.screening_scoring.resume_experience.preset_models import get_preset_model


class ScreeningActivityService:
    """把初筛评分的外部扇出收敛到活动检查点。"""

    def __init__(self, runner: ActivityRunner | None = None) -> None:
        self.runner = runner or ActivityRunner()

    def score_resume_experience(
        self, context: StepContext, core_input: ScoringCoreInput
    ) -> ActivityBatchResult:
        """逐段评分简历经历；恢复时仅补跑失败段并汇总全部已成功段。"""
        resume_profile = dict(core_input.resume_profile or {})
        job_profile = dict(core_input.job_profile or {})
        model = get_preset_model(
            str(job_profile.get("preset_model_id") or ""),
            str(job_profile.get("preset_model_version") or ""),
        )
        activities: list[ActivityDefinition] = []
        for index, raw_project in enumerate(list(resume_profile.get("experience_units", []))):
            project = dict(raw_project or {})
            project_id = str(project.get("experience_unit_id") or project.get("project_id") or "")
            # 缺失来源 ID 时仍保证同一冻结简历可稳定恢复，不能用随机 UUID。
            activity_key = project_id or f"position_{index}_{sha256(str(project).encode()).hexdigest()[:12]}"
            activities.append(ActivityDefinition(
                activity_key=f"resume_project:{activity_key}",
                input_data={"project": project, "model": {"id": model["model_id"], "version": model["version"]}},
                handler=lambda _activity, project=project: assess_resume_project(
                    project,
                    llm_config=dict(core_input.llm_config or {}),
                    preset_model=model,
                ),
                policy=ActivityPolicy(max_attempts=3, retry_after_seconds=15),
                # 单段经历的模型调用耗尽后，不能把其他已完成经历一并作废。
                # 回退仍返回正式的项目评分合同，只是明确标记本段未评估，聚合器
                # 不会把技术失败伪装成候选人的零能力。
                exhaustion_policy=ActivityExhaustionPolicy.FALLBACK_TO_STANDARD_MODEL,
                on_exhausted=lambda _activity, error, project=project: ActivityResolution(
                    outcome_kind=ActivityOutcomeKind.DEGRADED,
                    payload=build_unassessed_resume_project(
                        project,
                        reason_code="resume_project_activity_exhausted",
                        error_message=str(error),
                    ),
                    resolution_code="resume_project_unassessed",
                    quality_summary={
                        "usable": True,
                        "assessmentStatus": "unassessed",
                        "reasonCode": "resume_project_activity_exhausted",
                    },
                ),
                can_degrade=is_safe_model_degradation_error,
            ))
        batch = self.runner.run_many(
            workflow_run_id=context.workflow_run_id,
            worker_id=context.worker_id,
            parent_step_name=context.step_name,
            activities=activities,
            parallel=True,
        )
        if not batch.is_usable:
            return batch
        ordered_results = [batch.results[item.activity_key] for item in activities]
        result = assemble_resume_experience(
            resume_profile,
            ordered_results,
            llm_config=dict(core_input.llm_config or {}),
            preset_model=model,
        )
        return ActivityBatchResult(
            status=batch.status,
            results={"resume_experience": result},
            outcome_kinds=dict(batch.outcome_kinds),
            quality_summaries=dict(batch.quality_summaries),
        )

    def score_job_capabilities(
        self,
        context: StepContext,
        core_input: ScoringCoreInput,
        resume_experience_result: dict[str, Any],
    ) -> ActivityBatchResult:
        """按“证据类型 × LLM 配对批次”恢复岗位能力评分。

        一个 Activity 等于一次实际的模型批量请求，而不是一个单独配对。这样既不
        增加调用次数，也能让已成功的 WorkUnit、ProjectEvidence 或 SkillClaim
        批次从检查点复用；只有失败批次会等待重试或走保守的 ``unassessed`` 降级。
        """
        job_profile = dict(core_input.job_profile or {})
        from recruitment_ai_core.common.scoring_evidence import build_scoring_evidence

        scoring_evidence = build_scoring_evidence(
            resume_profile=dict(core_input.resume_profile or {}),
            resume_experience_result=resume_experience_result,
            preset_model_id=str(job_profile.get("preset_model_id") or ""),
            preset_model_version=str(job_profile.get("preset_model_version") or ""),
        )
        prepared = prepare_job_capability_pair_activities(
            job_profile, scoring_evidence, dict(core_input.llm_config or {}),
        )
        activities: list[ActivityDefinition] = []
        for item in prepared:
            activity_key = str(item["activity_key"])
            evidence_type = str(item["evidence_type"])
            pairs = list(item["pairs"])
            activities.append(ActivityDefinition(
                activity_key=activity_key,
                # 完整冻结配对进入输入哈希，确保任何来源变化都不能错误复用旧检查点。
                input_data={
                    "evidenceType": evidence_type,
                    "batchIndex": item["batch_index"],
                    "estimatedInputTokens": item.get("estimated_input_tokens"),
                    "estimatedOutputTokens": item.get("estimated_output_tokens"),
                    "pairs": pairs,
                    "llmConfig": dict(core_input.llm_config or {}),
                },
                handler=lambda _activity, pairs=pairs, evidence_type=evidence_type: (
                    score_job_capability_pair_activity(
                        pairs=pairs,
                        evidence_type=evidence_type,
                        llm_config=dict(core_input.llm_config or {}),
                    )
                ),
                policy=ActivityPolicy(max_attempts=3, retry_after_seconds=15),
                # 只有网络/服务类异常由 ActivityRunner 重试；耗尽后仅将本批配对
                # 标为未评估，不能把技术问题伪造成候选人的 L0 不匹配。
                exhaustion_policy=ActivityExhaustionPolicy.FALLBACK_TO_STANDARD_MODEL,
                on_exhausted=lambda _activity, error, pairs=pairs, evidence_type=evidence_type: ActivityResolution(
                    outcome_kind=ActivityOutcomeKind.DEGRADED,
                    payload=fallback_job_capability_pair_activity(
                        pairs=pairs,
                        evidence_type=evidence_type,
                        error_message=str(error),
                    ),
                    resolution_code="job_pair_batch_unassessed",
                    quality_summary={
                        "usable": True,
                        "assessmentStatus": "unassessed",
                        "reasonCode": "job_pair_activity_exhausted",
                    },
                ),
                can_degrade=is_safe_model_degradation_error,
            ))
        batch = self.runner.run_many(
            workflow_run_id=context.workflow_run_id,
            worker_id=context.worker_id,
            parent_step_name=context.step_name,
            activities=activities,
            parallel=True,
        )
        if not batch.is_usable:
            return batch
        outputs = {
            item.activity_key: batch.results[item.activity_key]
            for item in activities
        }
        result = assemble_job_capability_pair_activities(
            application_id="screening_core_scope",
            job_profile=job_profile,
            profile_version="screening_core_v1",
            stage="screening",
            activities=prepared,
            activity_outputs=outputs,
        )
        return ActivityBatchResult(
            status=batch.status,
            results={"job_capability": result},
            outcome_kinds=dict(batch.outcome_kinds),
            quality_summaries=dict(batch.quality_summaries),
        )

    def generate_presentation(
        self,
        context: StepContext,
        *,
        rule: RuleDerivedResult,
        core: Any,
        evidence_index: dict[str, Any],
    ) -> ActivityBatchResult:
        """以一个可恢复 Activity 生成全部展示文案。

        Bundle Activity 成功后由纯函数绑定 Signal/Target 与证据；若模型不可用，
        Activity 直接返回同形规则模板，不引入新的业务对象，也不改变前端 DTO。
        """
        service = PresentationGenerationService()
        rule_payload = asdict(rule)
        # 展示输入是运行时 DTO：从冻结岗位画像和原始评分中解析中文能力名、定义、
        # 事实理由与证据。它不落库，也绝不把 JDC_* 之类内部 ID 交给页面文案层。
        prepared = build_assessment_presentation_inputs(
            stage="screening",
            core_result={
                "preset_experience_result": dict(getattr(core, "preset_experience_result", {}) or {}),
                "job_result": dict(getattr(core, "job_result", {}) or {}),
                "score_result": dict(getattr(core, "score_result", {}) or {}),
                "capability_graph": dict(getattr(core, "capability_graph", {}) or {}),
            },
            rule_result=rule_payload,
            job_profile=dict(getattr(core, "job_profile", {}) or {}),
            interview_targets=list(rule.target_updates),
        )
        batch = self.runner.run_many(
            workflow_run_id=context.workflow_run_id,
            worker_id=context.worker_id,
            parent_step_name=context.step_name,
            activities=[
                ActivityDefinition(
                    activity_key="presentation_bundle",
                    input_data={"kind": "presentation_bundle", "bundleInput": dict(prepared["bundle_input"])},
                    handler=lambda _activity: service.generate_screening_bundle(
                        rule=rule, presentation_input=prepared,
                    ),
                    policy=ActivityPolicy(max_attempts=1, retry_after_seconds=1),
                    exhaustion_policy=ActivityExhaustionPolicy.FALLBACK_TO_STANDARD_MODEL,
                    on_exhausted=lambda _activity, error: ActivityResolution(
                        outcome_kind=ActivityOutcomeKind.DEGRADED,
                        payload=service.fallback_screening_bundle(
                            rule=rule, presentation_input=prepared,
                        ),
                        resolution_code="screening_presentation_rule_fallback",
                        quality_summary={
                            "usable": True,
                            "generationMode": "rule_fallback",
                            "reasonCode": "presentation_activity_exhausted",
                        },
                    ),
                    can_degrade=is_safe_model_degradation_error,
                ),
            ],
        )
        if not batch.is_usable:
            return batch
        result: PresentationResult = service.assemble_screening_bundle(
            rule=rule,
            bundle=batch.results["presentation_bundle"],
            presentation_input=prepared,
        )
        return ActivityBatchResult(
            status=batch.status,
            results={"presentation": asdict(result)},
            outcome_kinds=dict(batch.outcome_kinds),
            quality_summaries=dict(batch.quality_summaries),
        )
