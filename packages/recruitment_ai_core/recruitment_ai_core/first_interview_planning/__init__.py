"""一面题单纯算法入口。"""
from .constraint_builder import build_question_planning_constraints
from .llm_adapter import try_generate_question_proposals
from .rule_derivation import FirstInterviewRuleDerivationService
from .result_contracts import (
    FirstInterviewPlanPresentationResult,
    QuestionDraftProposal,
    QuestionPlanningConstraintSet,
    QuestionProposalGenerationResult,
    QuestionRuleDerivationResult,
)

__all__ = [
    "FirstInterviewPlanPresentationResult",
    "QuestionDraftProposal",
    "QuestionPlanningConstraintSet",
    "QuestionProposalGenerationResult",
    "QuestionRuleDerivationResult",
    "build_question_planning_constraints",
    "try_generate_question_proposals",
    "FirstInterviewRuleDerivationService",
]