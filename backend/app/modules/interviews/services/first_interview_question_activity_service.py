"""一面题目草案的活动级恢复服务。"""
from __future__ import annotations

from dataclasses import asdict

from backend.app.infrastructure.workflow_runtime import (
    ActivityBatchResult,
    ActivityDefinition,
    ActivityPolicy,
    ActivityResolution,
    ActivityRunner,
    is_safe_model_degradation_error,
)
from backend.app.modules.interviews.services.first_interview_question_proposal_service import (
    FirstInterviewQuestionProposalService,
)
from backend.app.shared.workflows import ActivityExhaustionPolicy, ActivityOutcomeKind, StepContext
from recruitment_ai_core.first_interview_planning import (
    QuestionPlanningConstraintSet,
    QuestionProposalGenerationResult,
)
from recruitment_ai_core.first_interview_planning.contracts import FirstInterviewPlanningInput


class FirstInterviewQuestionActivityService:
    """把昂贵的题目提案 LLM 调用从 Workflow Step 中独立为一个 Activity。"""

    def __init__(self, runner: ActivityRunner | None = None) -> None:
        self.runner = runner or ActivityRunner()

    def generate(
        self,
        context: StepContext,
        *,
        planning_input: FirstInterviewPlanningInput,
        constraints: QuestionPlanningConstraintSet,
    ) -> ActivityBatchResult:
        """生成或复用同一冻结输入下的题目草案，不发布题单版本。"""
        def fallback(_activity, error: Exception) -> ActivityResolution:
            # 规则层会根据同一 question_slots 生成确定性题目；这里不伪造题干。
            return ActivityResolution(
                outcome_kind=ActivityOutcomeKind.DEGRADED,
                payload=asdict(QuestionProposalGenerationResult(
                    proposals=[],
                    generation_mode="rule_fallback",
                    generation_warnings=["first_interview_activity_exhausted_rule_fallback"],
                    llm_trace={"errorType": type(error).__name__},
                )),
                resolution_code="first_interview_question_rule_fallback",
                quality_summary={"usable": True, "degraded": True, "source": "rule_fallback", "errorType": type(error).__name__},
            )

        activity = ActivityDefinition(
            activity_key="question_proposals",
            input_data={
                "planningInput": asdict(planning_input),
                "constraints": asdict(constraints),
            },
            handler=lambda _activity: asdict(
                FirstInterviewQuestionProposalService().generate(
                    planning_input=planning_input,
                    constraints=constraints,
                )
            ),
            policy=ActivityPolicy(max_attempts=3, retry_after_seconds=15),
            exhaustion_policy=ActivityExhaustionPolicy.FALLBACK_TO_STANDARD_MODEL,
            on_exhausted=fallback,
            can_degrade=is_safe_model_degradation_error,
        )
        return self.runner.run_many(
            workflow_run_id=context.workflow_run_id,
            worker_id=context.worker_id,
            parent_step_name=context.step_name,
            activities=[activity],
        )
