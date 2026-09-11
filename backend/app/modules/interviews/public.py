"""Interview 对外稳定入口：跨模块调用必须经由此处的显式惰性导出。"""
from __future__ import annotations

from importlib import import_module

_LAZY_EXPORTS = {
    "InterviewLifecycleCommands": ("backend.app.modules.interviews.commands.interview_lifecycle_commands", "InterviewLifecycleCommands"),
    "InterviewRecordCommands": ("backend.app.modules.interviews.commands.interview_record_commands", "InterviewRecordCommands"),
    "InterviewWorkflowRequestCommands": ("backend.app.modules.interviews.commands.interview_workflow_request_commands", "InterviewWorkflowRequestCommands"),
    "FirstInterviewQueryService": ("backend.app.modules.interviews.queries.first_interview_query_service", "FirstInterviewQueryService"),
    "SecondInterviewQueryService": ("backend.app.modules.interviews.queries.second_interview_query_service", "SecondInterviewQueryService"),
    "FinalReviewView": ("backend.app.modules.interviews.schemas.view_schemas", "FinalReviewView"),
    "FirstInterviewEvaluationView": ("backend.app.modules.interviews.schemas.view_schemas", "FirstInterviewEvaluationView"),
    "FirstInterviewPlanView": ("backend.app.modules.interviews.schemas.view_schemas", "FirstInterviewPlanView"),
    "FirstInterviewWorkspaceView": ("backend.app.modules.interviews.schemas.view_schemas", "FirstInterviewWorkspaceView"),
    "SecondInterviewReviewView": ("backend.app.modules.interviews.schemas.view_schemas", "SecondInterviewReviewView"),
    "SecondInterviewWorkspaceView": ("backend.app.modules.interviews.schemas.view_schemas", "SecondInterviewWorkspaceView"),
    "build_non_capability_card": ("backend.app.modules.interviews.readModel.non_capability_read_model", "build_non_capability_card"),
    "interview_parse_result_view": ("backend.app.modules.interviews.services.interview_persistence_views", "interview_parse_result_view"),
    "interview_record_view": ("backend.app.modules.interviews.services.interview_persistence_views", "interview_record_view"),
    "interview_target_view": ("backend.app.modules.interviews.services.interview_persistence_views", "interview_target_view"),
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