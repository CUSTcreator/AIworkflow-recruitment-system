"""候选人岗位分发的模型活动边界。

专业到岗位的匹配目前是一整次批量 LLM 请求，因此它是一个 Activity；它的结果只作为
岗位分发 Workflow 的草稿，正式 Application 仍由后续发布步骤在短事务中创建。
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
from backend.app.modules.candidates.intake_service import CandidateIntakeService
from backend.app.shared.workflows import ActivityExhaustionPolicy, ActivityOutcomeKind, StepContext
from recruitment_ai_core.candidate_routing import JobMajorRequirement, manual_selection_decisions


class CandidateRoutingActivityService:
    """将岗位匹配 LLM 调用纳入活动级检查点、重试和幂等恢复。"""

    def __init__(self, runner: ActivityRunner | None = None) -> None:
        self._runner = runner or ActivityRunner()

    def decide(
        self,
        context: StepContext,
        *,
        routing_input: dict[str, Any],
        source_quote: str,
    ) -> ActivityBatchResult:
        """执行或复用唯一的批量岗位匹配活动。"""
        def fallback(_activity, error: Exception) -> ActivityResolution:
            # 自动匹配不可用时保留无专业限制岗位的确定性结果，其余岗位进入
            # 人工选择。降级输出仍是正常决策合同，发布步骤可安全处理部分成功。
            requirements = [
                JobMajorRequirement(
                    str(item["jobId"]),
                    str(item["jdVersionId"]),
                    str(item["title"]),
                    str(item.get("majorRequirement") or ""),
                )
                for item in routing_input.get("requirements") or []
                if isinstance(item, dict)
            ]
            decision = {
                "status": "completed",
                "major": str(routing_input.get("major") or ""),
                "reason": "自动岗位匹配暂不可用，需人工选择岗位",
                "decisions": manual_selection_decisions(
                    jobs=requirements,
                    reason="自动岗位匹配暂不可用，需人工选择岗位",
                ),
                "requirements": list(routing_input.get("requirements") or []),
                "trace": {"mode": "routing_activity_fallback", "errorType": type(error).__name__},
            }
            return ActivityResolution(
                outcome_kind=ActivityOutcomeKind.DEGRADED,
                payload={"decision": decision},
                resolution_code="candidate_routing_manual_selection_fallback",
                quality_summary={"usable": True, "degraded": True, "status": decision["status"], "errorType": type(error).__name__},
            )

        return self._runner.run_many(
            workflow_run_id=context.workflow_run_id,
            worker_id=context.worker_id,
            parent_step_name=context.step_name,
            activities=[
                ActivityDefinition(
                    activity_key="candidate_major_routing",
                    input_data={
                        "routingInput": dict(routing_input),
                        "sourceQuote": source_quote,
                    },
                    handler=lambda _activity: {
                        "decision": CandidateIntakeService.decide_routing(
                            routing_input,
                            source_quote=source_quote,
                        )
                    },
                    policy=ActivityPolicy(max_attempts=3, retry_after_seconds=15),
                    exhaustion_policy=ActivityExhaustionPolicy.FALLBACK_TO_STANDARD_MODEL,
                    on_exhausted=fallback,
                    can_degrade=is_safe_model_degradation_error,
                )
            ],
        )
