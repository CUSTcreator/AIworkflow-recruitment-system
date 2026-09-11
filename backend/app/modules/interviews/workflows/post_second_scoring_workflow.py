"""二面后 V3 评分的 Workflow 计划声明。"""
from backend.app.modules.assessment.public import AssessmentStage
from backend.app.modules.interviews.workflows.post_interview_scoring_workflow import (
    build_post_interview_scoring_spec,
)


def build_post_second_scoring_spec(transition_handler, blocked_handler=None):
    """声明二面后 V3 评分。

    共用七步计划位于 ``post_interview_scoring_workflow.py``：冻结 V2 Snapshot 和二面记录 →
    面评断言 → 锚点评估 → 拓扑增量评分 → 规则派生 → 展示加工 → 原子发布 V3。
    """

    return build_post_interview_scoring_spec(
        workflow_type="post_second_scoring_workflow",
        stage=AssessmentStage.AFTER_SECOND_INTERVIEW,
        previous_stage=AssessmentStage.AFTER_FIRST_INTERVIEW,
        kind="second",
        actions=frozenset({"submit_second_feedback", "complete_second_interview"}),
        transition_handler=transition_handler,
        blocked_handler=blocked_handler,
    )
