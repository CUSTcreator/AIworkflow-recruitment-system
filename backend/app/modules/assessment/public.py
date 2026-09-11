"""Assessment 对外稳定入口：跨模块调用必须经由此处的显式惰性导出。"""
from __future__ import annotations

from importlib import import_module

_LAZY_EXPORTS = {
    "AssessmentInputSource": ("backend.app.modules.assessment.readModel.input_source", "AssessmentInputSource"),
    "ScoringRequestCommands": ("backend.app.modules.assessment.commands.scoring_request_commands", "ScoringRequestCommands"),
    "enqueue_initial_assessment": ("backend.app.modules.assessment.services.assessment_scheduling_service", "enqueue_initial_assessment"),
    "enqueue_resume_rebuild_rescoring": ("backend.app.modules.assessment.services.assessment_scheduling_service", "enqueue_resume_rebuild_rescoring"),
    "ScreeningQueryService": ("backend.app.modules.assessment.queries.read_models", "ScreeningQueryService"),
    "DecisionSummaryQueryService": ("backend.app.modules.assessment.queries.decision_summary", "DecisionSummaryQueryService"),
    "build_candidate_decision_overview": ("backend.app.modules.assessment.queries.decision_overview", "build_candidate_decision_overview"),
    "build_decision_support": ("backend.app.modules.assessment.queries.decision_support", "build_decision_support"),
    "screening_summary_view": ("backend.app.modules.assessment.readModel.presenters", "screening_summary_view"),
    "CandidateDecisionOverviewView": ("backend.app.modules.assessment.schemas.view_schemas", "CandidateDecisionOverviewView"),
    "DecisionSupportView": ("backend.app.modules.assessment.schemas.view_schemas", "DecisionSupportView"),
    "EvidenceDetailView": ("backend.app.modules.assessment.schemas.view_schemas", "EvidenceDetailView"),
    "HardScreeningReviewView": ("backend.app.modules.assessment.schemas.view_schemas", "HardScreeningReviewView"),
    "ScreeningReviewView": ("backend.app.modules.assessment.schemas.view_schemas", "ScreeningReviewView"),
    "AssessmentSource": ("backend.app.modules.assessment.services.source_service", "AssessmentSource"),
    "AssessmentSourceService": ("backend.app.modules.assessment.services.source_service", "AssessmentSourceService"),
    "AssessmentStage": ("backend.app.modules.assessment.domain.assessment_version", "AssessmentStage"),
    "AssessmentSourceManifest": ("backend.app.modules.assessment.domain.assessment_version", "AssessmentSourceManifest"),
    "IncrementalPresentationService": ("backend.app.modules.assessment.services.incremental_presentation_service", "IncrementalPresentationService"),
    "IncrementalRuleDerivationService": ("backend.app.modules.assessment.services.incremental_rule_derivation_service", "IncrementalRuleDerivationService"),
    "PostInterviewAssessmentPublisher": ("backend.app.modules.assessment.services.post_interview_assessment_publisher", "PostInterviewAssessmentPublisher"),
    "decision_summary": ("backend.app.modules.assessment.queries.assessment_version_read_model", "decision_summary"),
    "decision_summary_view": ("backend.app.modules.assessment.queries.assessment_version_read_model", "decision_summary_view"),
}

def __getattr__(name: str):
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = target
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value

__all__ = list(_LAZY_EXPORTS)
