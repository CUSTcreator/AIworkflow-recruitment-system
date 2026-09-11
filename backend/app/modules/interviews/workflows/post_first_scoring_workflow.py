"""一面后 V2 评分的 Workflow 计划声明。"""
from backend.app.modules.assessment.public import AssessmentStage
from backend.app.modules.interviews.workflows.post_interview_scoring_workflow import (
    build_post_interview_scoring_spec,
)


def build_post_first_scoring_spec(transition_handler, blocked_handler=None):
    """声明一面后 V2 评分。

    共用七步计划位于 ``post_interview_scoring_workflow.py``：冻结 V1 Snapshot 和一面记录 →
    面评断言 → 锚点评估 → 拓扑增量评分 → 规则派生 → 展示加工 → 原子发布 V2。
    """

    return build_post_interview_scoring_spec(
        workflow_type="post_first_scoring_workflow",
        stage=AssessmentStage.AFTER_FIRST_INTERVIEW,
        previous_stage=AssessmentStage.SCREENING,
        kind="first",
        actions=frozenset({"submit_first_feedback", "complete_first_interview"}),
        transition_handler=transition_handler,
        blocked_handler=blocked_handler,
    )
