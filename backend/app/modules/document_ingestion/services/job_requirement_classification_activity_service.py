"""岗位确认前的任职要求分类活动。

每个 Activity 可以携带多个岗位，但请求和返回始终以 ``job_key`` 隔离岗位边界。
模型只分类每条原文要求；硬筛规则由经过本地校验的分类结果确定性派生，避免让模型
直接编造一套可淘汰候选人的策略。
"""
from __future__ import annotations

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
from recruitment_ai_core.job_capability.requirement_classification import (
    classify_job_requirements_batch,
    deterministic_requirement_fallback,
)
from recruitment_ai_core.llm_budget import LlmBudgetPolicy, estimate_tokens, pack_llm_batches


class JobRequirementClassificationActivityService:
    """按岗位批次执行分类，Activity 失败时只保留确定性规则结果。"""

    def __init__(self, runner: ActivityRunner | None = None) -> None:
        self.runner = runner or ActivityRunner()

    @staticmethod
    def _row_ref(job: dict[str, Any], index: int) -> str:
        metadata = dict(job.get("metadata") or {})
        source_cells = dict(metadata.get("source_cells") or {})
        return str(next(iter(source_cells), f"row_{index}"))

    @staticmethod
    def _job_input(job: dict[str, Any], row_ref: str) -> dict[str, Any]:
        # 专业字段仅用于识别任职资格中已提取过的专业条款；不把整份解析工件发送给模型。
        metadata = dict(job.get("metadata") or {})
        return {
            "job_key": row_ref,
            "title": str(job.get("title") or ""),
            "education_requirement": job.get("education_requirement"),
            "major_requirement": job.get("major_requirement"),
            "major_requirement_source_quote": metadata.get("major_source_quote"),
            "qualifications": list(job.get("qualifications") or []),
        }

    @staticmethod
    def _fallback(job_input: dict[str, Any], error: Exception) -> dict[str, Any]:
        result = deterministic_requirement_fallback(job_input)
        result["degraded"] = True
        result["traces"] = [{
            "stage": "job_requirement_classification",
            "outcome": "degraded",
            "resolution_code": "job_requirement_deterministic_fallback",
            "error_type": type(error).__name__,
        }]
        return result

    def classify(
        self,
        context: StepContext,
        jobs: list[dict[str, Any]],
        *,
        source_document_id: str,
        source_sha256: str,
    ) -> ActivityBatchResult:
        inputs = [
            self._job_input(job, self._row_ref(job, index))
            for index, job in enumerate(jobs, start=1)
        ]
        # 小文件通常一次请求即可；文件变大时按 token 预算拆成多个岗位批次。
        batches = pack_llm_batches(
            inputs,
            prompt_tokens=900,
            schema_tokens=700,
            policy=LlmBudgetPolicy(max_items=12),
            estimate_item_input=lambda item: estimate_tokens(item),
            estimate_item_output=lambda _item: 180,
        )
        definitions: list[ActivityDefinition] = []
        for batch in batches:
            batch_inputs = [dict(item) for item in batch.items]

            def exhausted(_activity, error, batch_inputs=batch_inputs):
                rows = []
                for item in batch_inputs:
                    fallback = self._fallback(item, error)
                    rows.append({
                        "job_key": item["job_key"],
                        "classification": fallback,
                        "hardScreeningRules": list(
                            fallback.get("hardScreeningRules") or []
                        ),
                        "degraded": True,
                    })
                return ActivityResolution(
                    outcome_kind=ActivityOutcomeKind.DEGRADED,
                    payload={"rows": rows, "degraded": True},
                    resolution_code="job_requirement_batch_rule_fallback",
                    quality_summary={
                        "usable": True,
                        "degraded": True,
                        "jobCount": len(batch_inputs),
                    },
                )

            definitions.append(ActivityDefinition(
                activity_key=f"requirement_classification:batch:{batch.batch_index + 1:03d}",
                input_data={
                    "sourceDocumentId": source_document_id,
                    "sourceSha256": source_sha256,
                    "jobs": batch_inputs,
                    "estimatedInputTokens": batch.estimated_input_tokens,
                    "estimatedOutputTokens": batch.estimated_output_tokens,
                },
                handler=lambda _activity, batch_inputs=batch_inputs: self._classify_batch(
                    batch_inputs
                ),
                policy=ActivityPolicy(max_attempts=2, retry_after_seconds=15),
                exhaustion_policy=ActivityExhaustionPolicy.FALLBACK_TO_STANDARD_MODEL,
                on_exhausted=exhausted,
                can_degrade=is_safe_model_degradation_error,
            ))

        if not definitions:
            return ActivityBatchResult(
                status=ActivityOutcomeKind.COMPLETED.value,
                results={"classification": {"rows": [], "degraded": False}},
            )

        batch_result = self.runner.run_many(
            workflow_run_id=context.workflow_run_id,
            worker_id=context.worker_id,
            parent_step_name=context.step_name,
            activities=definitions,
            parallel=True,
        )
        if not batch_result.is_usable:
            return batch_result

        rows: list[dict[str, Any]] = []
        for definition in definitions:
            result = dict(batch_result.results.get(definition.activity_key) or {})
            rows.extend(list(result.get("rows") or []))
        return ActivityBatchResult(
            status=batch_result.status,
            results={
                "classification": {
                    "sourceDocumentId": source_document_id,
                    "sourceSha256": source_sha256,
                    "rows": rows,
                    "degraded": batch_result.status == ActivityOutcomeKind.DEGRADED.value,
                }
            },
            outcome_kinds=batch_result.outcome_kinds,
            quality_summaries=batch_result.quality_summaries,
        )

    @staticmethod
    def _classify_batch(batch_inputs: list[dict[str, Any]]) -> dict[str, Any]:
        """一次批量请求；核心函数负责统一 Prompt、Schema 和引用校验。"""
        result = classify_job_requirements_batch(batch_inputs)
        rows: list[dict[str, Any]] = []
        for item in result.get("jobs") or []:
            rows.append({
                "job_key": item["job_key"],
                "classification": item,
                "hardScreeningRules": list(item.get("hardScreeningRules") or []),
                "degraded": bool(item.get("degraded")),
            })
        return {"rows": rows, "degraded": bool(result.get("degraded"))}
