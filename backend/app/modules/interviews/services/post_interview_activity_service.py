"""V2/V3 面评后高风险计算的活动级恢复边界。

本服务只封装可重试、可能调用模型的计算，并把 JSON 结果交回 Workflow。面评正式
解析结果、评估版本、Target、Interview 和 Application 状态始终由最后发布 Step 写入。
"""

from __future__ import annotations

from typing import Any, Sequence

from backend.app.infrastructure.workflow_runtime import (
    ActivityBatchResult,
    ActivityResolution,
    ActivityDefinition,
    ActivityPolicy,
    ActivityRunner,
    is_safe_model_degradation_error,
)
from backend.app.modules.assessment.public import (
    IncrementalPresentationService,
)
from backend.app.modules.assessment.public import AssessmentSource
from backend.app.shared.workflows import (
    StepContext,
    ActivityExhaustionPolicy,
    ActivityOutcomeKind,
)

from recruitment_ai_core.interview_evaluation.anchor_evaluation import (
    compact_anchor,
    evaluate_anchor_batch,
)
from recruitment_ai_core.interview_evaluation import (
    InterviewEvidenceItem,
    InterviewEvidenceExtractionResult,
    InterviewParseInput,
    extract_and_bind_interview_evidence,
)


POST_INTERVIEW_MODEL_WORKFLOWS = {
    "post_first_scoring_workflow": "post_first_scoring",
    "post_second_scoring_workflow": "post_second_scoring",
}


def post_interview_model_workflow_name(workflow_type: str) -> str:
    """把运行时 Workflow 名映射到唯一的 LLM 配置键。"""
    try:
        return POST_INTERVIEW_MODEL_WORKFLOWS[workflow_type]
    except KeyError as error:
        raise ValueError(
            f"post_interview_model_workflow_unknown:{workflow_type}"
        ) from error


def plan_anchor_evaluation_batches(
    *,
    units: Sequence[dict[str, Any]],
    anchors: Sequence[dict[str, Any]],
) -> list[tuple[list[dict[str, Any]], list[dict[str, Any]]]]:
    """在固定 token 合同内对 ``unit x anchor`` 矩阵做稳定二维切分。"""
    from recruitment_ai_core.llm_budget import (
        LlmBudgetPolicy,
        estimate_tokens,
        pack_llm_batches,
    )

    policy = LlmBudgetPolicy(max_items=12)
    anchor_batches = pack_llm_batches(
        list(anchors),
        policy=policy,
        prompt_tokens=700,
        schema_tokens=450,
        estimate_item_input=lambda item: estimate_tokens(compact_anchor(item)),
        # 第一维只负责保证锚点集合本身可装入；第二维再按实际 unit 数量
        # 核算完整矩阵输出，不能把这部分预算重复累计。
        estimate_item_output=lambda _item: 40,
    )
    result: list[tuple[list[dict[str, Any]], list[dict[str, Any]]]] = []
    for anchor_batch in anchor_batches:
        batch_anchors = [dict(item) for item in anchor_batch.items]
        compact_anchors = [compact_anchor(item) for item in batch_anchors]
        unit_batches = pack_llm_batches(
            list(units),
            policy=policy,
            prompt_tokens=700 + estimate_tokens(compact_anchors),
            schema_tokens=450,
            estimate_item_input=lambda item: estimate_tokens(
                {
                    "unitId": item.get("unitId") or item.get("unit_id"),
                    "text": item.get("text"),
                }
            ),
            estimate_item_output=lambda _item: max(128, len(batch_anchors) * 40),
        )
        result.extend(
            ([dict(item) for item in unit_batch.items], batch_anchors)
            for unit_batch in unit_batches
        )
    return result


class PostInterviewActivityService:
    """V2/V3 的单个外部/模型计算均经由同一活动运行器执行。"""

    def __init__(self, runner: ActivityRunner | None = None) -> None:
        self.runner = runner or ActivityRunner()

    def _run(
        self,
        context: StepContext,
        *,
        activity_key: str,
        input_data: dict[str, Any],
        handler,
        max_attempts: int = 3,
        exhaustion_policy: ActivityExhaustionPolicy = ActivityExhaustionPolicy.FAIL_AS_SYSTEM_ERROR,
        on_exhausted=None,
    ) -> ActivityBatchResult:
        return self.runner.run_many(
            workflow_run_id=context.workflow_run_id,
            worker_id=context.worker_id,
            parent_step_name=context.step_name,
            activities=[
                ActivityDefinition(
                    activity_key=activity_key,
                    input_data=input_data,
                    handler=handler,
                    policy=ActivityPolicy(
                        max_attempts=max_attempts, retry_after_seconds=15
                    ),
                    exhaustion_policy=exhaustion_policy,
                    on_exhausted=on_exhausted,
                    can_degrade=(
                        is_safe_model_degradation_error
                        if exhaustion_policy
                        != ActivityExhaustionPolicy.FAIL_AS_SYSTEM_ERROR
                        else None
                    ),
                )
            ],
        )

    def extract_and_bind_evidence(
        self, context: StepContext, parse_input: InterviewParseInput
    ) -> ActivityBatchResult:
        """执行唯一面评语义 LLM 活动，产物由下游本地步骤规范化。"""

        def fallback(_activity, error: Exception) -> ActivityResolution:
            # 保留每条可定位的原文，但降为非评分证据；不能把模型不可用伪装成能力判断。
            items = []
            source_ids = []
            for index, record in enumerate(parse_input.interview_records, start=1):
                record_id = str(
                    record.get("record_id") or record.get("recordId") or ""
                ).strip()
                text = str(
                    record.get("analysis_text")
                    or record.get("raw_text")
                    or record.get("answer")
                    or record.get("response")
                    or record.get("notes")
                    or record.get("text")
                    or ""
                ).strip()
                if not record_id:
                    continue
                source_ids.append(record_id)
                if text:
                    items.append(
                        InterviewEvidenceItem(
                            evidence_id=f"IE_{record_id}_{index}",
                            record_id=record_id,
                            source_refs=({"record_id": record_id, "quote": text},),
                            disposition="non_scoring",
                            text=text,
                        )
                    )
            extraction = InterviewEvidenceExtractionResult(
                parser_version="interview_evidence_v4",
                source_record_ids=tuple(source_ids),
                items=tuple(items),
                no_new_evidence=not items,
            ).as_dict()
            # 与正常 Activity 完全同形，保证下游可直接反序列化并继续规范化。
            payload = {
                "parseInput": parse_input.as_dict(),
                "evidenceExtraction": extraction,
            }
            return ActivityResolution(
                outcome_kind=ActivityOutcomeKind.DEGRADED,
                payload=payload,
                resolution_code="interview_evidence_extraction_rule_fallback",
                quality_summary={
                    "usable": True,
                    "degraded": True,
                    "keptRecordCount": len(items),
                    "reasonCode": "interview_evidence_activity_exhausted",
                    "errorType": type(error).__name__,
                },
            )

        return self._run(
            context,
            activity_key="interview_evidence_extraction",
            input_data={
                "records": [
                    {
                        "record_id": str(
                            item.get("record_id") or item.get("recordId") or ""
                        ),
                        "text": str(
                            item.get("analysis_text")
                            or item.get("raw_text")
                            or item.get("answer")
                            or item.get("response")
                            or item.get("notes")
                            or item.get("text")
                            or ""
                        ).strip(),
                    }
                    for item in parse_input.interview_records
                ]
            },
            max_attempts=2,
            exhaustion_policy=ActivityExhaustionPolicy.FALLBACK_TO_STANDARD_MODEL,
            on_exhausted=fallback,
            handler=lambda _activity: {
                "parseInput": parse_input.as_dict(),
                "evidenceExtraction": extract_and_bind_interview_evidence(
                    parse_input
                ).as_dict(),
            },
        )

    def evaluate_anchor_side(
        self,
        context: StepContext,
        *,
        side: str,
        units: Sequence[dict[str, Any]],
        anchors: Sequence[dict[str, Any]],
        workflow_type: str,
    ) -> ActivityBatchResult:
        """按 token 预算为一侧创建批次 Activity；不允许空矩阵降级。"""
        model_workflow_name = post_interview_model_workflow_name(workflow_type)
        batches = plan_anchor_evaluation_batches(units=units, anchors=anchors)
        activities = []
        for batch_index, (batch_units, batch_anchors) in enumerate(batches):
            key = f"interview_anchor_judgement:{side}:{batch_index:03d}"
            activities.append(
                ActivityDefinition(
                    activity_key=key,
                    input_data={
                        "side": side,
                        "batchIndex": batch_index,
                        "units": batch_units,
                        "anchors": [compact_anchor(x) for x in batch_anchors],
                    },
                    handler=lambda _activity, u=batch_units, a=batch_anchors: evaluate_anchor_batch(
                        units=u,
                        anchors=a,
                        workflow_name=model_workflow_name,
                    ),
                    policy=ActivityPolicy(max_attempts=2, retry_after_seconds=15),
                )
            )
        return self.runner.run_many(
            workflow_run_id=context.workflow_run_id,
            worker_id=context.worker_id,
            parent_step_name=context.step_name,
            activities=activities,
            parallel=True,
        )

    def prepare_presentation(
        self,
        *,
        source: AssessmentSource,
        core_result: Any,
        rule_result: dict[str, Any],
    ) -> dict[str, Any]:
        """构造共享展示输入；这是本地纯函数，不属于 ExternalActivity。"""
        return dict(
            IncrementalPresentationService().prepare_input(
                source=source,
                core_result=core_result,
                rule_result=rule_result,
            )
        )

    def generate_presentation_bundle(
        self,
        context: StepContext,
        *,
        source: AssessmentSource,
        core_result: Any,
        rule_result: dict[str, Any],
        presentation_input: dict[str, Any],
    ) -> ActivityBatchResult:
        """V2/V3 的唯一展示 ExternalActivity：一次调用产出推荐和全部文案。"""
        service = IncrementalPresentationService()
        return self._run(
            context,
            activity_key="incremental_presentation_bundle",
            input_data={
                "sourceManifest": source.source_manifest.model_dump(mode="json"),
                "coreResult": core_result.as_dict(),
                "ruleResult": dict(rule_result),
                "presentationInput": dict(presentation_input),
            },
            handler=lambda _activity: {
                "bundle": dict(
                    service.generate_bundle(
                        stage=source.stage.value,
                        presentation_input=presentation_input,
                    )
                )
            },
            max_attempts=2,
            exhaustion_policy=ActivityExhaustionPolicy.FALLBACK_TO_STANDARD_MODEL,
            on_exhausted=lambda _activity, error: ActivityResolution(
                outcome_kind=ActivityOutcomeKind.DEGRADED,
                payload={
                    "bundle": dict(
                        service.fallback_bundle(presentation_input=presentation_input)
                    )
                },
                resolution_code="incremental_presentation_rule_fallback",
                quality_summary={
                    "usable": True,
                    "degraded": True,
                    "generationMode": "rule_fallback",
                    "reasonCode": "presentation_activity_exhausted",
                    "errorType": type(error).__name__,
                },
            ),
        )

    @staticmethod
    def assemble_presentation(
        *,
        source: AssessmentSource,
        core_result: Any,
        rule_result: dict[str, Any],
        presentation_input: dict[str, Any],
        bundle: dict[str, Any],
    ) -> dict[str, Any]:
        """纯组装唯一 Bundle Activity 产物，不访问外部服务。"""
        return dict(
            IncrementalPresentationService().assemble(
                source=source,
                core_result=core_result,
                rule_result=rule_result,
                presentation_input=presentation_input,
                bundle=bundle,
            )
        )
