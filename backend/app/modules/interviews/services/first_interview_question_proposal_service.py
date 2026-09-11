"""一面题单 LLM 核心产出服务。"""
from __future__ import annotations

from recruitment_ai_core.first_interview_planning import (
    QuestionPlanningConstraintSet,
    QuestionProposalGenerationResult,
    try_generate_question_proposals,
)
from recruitment_ai_core.first_interview_planning.contracts import FirstInterviewPlanningInput


class FirstInterviewQuestionProposalService:
    """题单 LLM 提案边界服务。

    输入是已冻结的 FirstInterviewPlanningInput 和 QuestionPlanningConstraintSet，输出仅是
    QuestionProposalGenerationResult。这里不读取 ORM、不写数据库，也不允许改写 Target 或槽位约束。
    """

    def generate(
        self,
        *,
        planning_input: FirstInterviewPlanningInput,
        constraints: QuestionPlanningConstraintSet,
    ) -> QuestionProposalGenerationResult:
        return try_generate_question_proposals(planning_input, constraints)