"""Candidate 对外稳定入口。

本模块拥有 Candidate、ResumeSubmission、SourceDocument、ResumeProfile 的简历管理
生命周期。契约 DTO 会立即导出；历史 Service 按需加载，避免跨模块导入时把对象存储、
WorkflowRuntime 等基础设施一起加载。其他模块不得直接修改 Candidate/Submission 状态。
"""
from __future__ import annotations

from importlib import import_module

from backend.app.modules.candidates.contracts import (
    CandidateResumeRebuildView,
    PublishedResumeSnapshot,
    get_candidate_resume_rebuild_view,
    get_published_resume_snapshot,
)
from backend.app.modules.candidates.resume_submission_status import ResumeIntakeMode, ResumeSubmissionStatus, transition_submission_status

_LAZY_EXPORTS = {
    "CandidateIdentity": ("backend.app.modules.candidates.identity_service", "CandidateIdentity"),
    "CandidateIdentityService": ("backend.app.modules.candidates.identity_service", "CandidateIdentityService"),
    "normalize_email": ("backend.app.modules.candidates.identity_service", "normalize_email"),
    "normalize_phone": ("backend.app.modules.candidates.identity_service", "normalize_phone"),
    "candidate_profile_view": ("backend.app.modules.candidates.profile_views", "candidate_profile_view"),
    "CandidateIntakeService": ("backend.app.modules.candidates.intake_service", "CandidateIntakeService"),
    "CandidateResumeRebuildService": ("backend.app.modules.candidates.rebuild_service", "CandidateResumeRebuildService"),
    "ResumeReplacementService": ("backend.app.modules.candidates.replacement_service", "ResumeReplacementService"),
    "resume_waiting_routing_for_job_profile": ("backend.app.modules.candidates.candidate_routing_service", "resume_waiting_routing_for_job_profile"),
    "CandidateIntakeProcessService": ("backend.app.modules.candidates.intake_process_service", "CandidateIntakeProcessService"),
    "CandidateRoutingService": ("backend.app.modules.candidates.candidate_routing_service", "CandidateRoutingService"),
    "ResumeFailureKind": ("backend.app.modules.candidates.domain.resume_submission_state_machine", "ResumeFailureKind"),
    "ResumeReviewKind": ("backend.app.modules.candidates.domain.resume_submission_state_machine", "ResumeReviewKind"),
}


def __getattr__(name: str):
    """兼容历史公开能力；仅在实际使用时加载其内部实现。"""
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = target
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value


__all__ = [
    "ResumeIntakeMode", "ResumeSubmissionStatus", "transition_submission_status",
    "PublishedResumeSnapshot", "CandidateResumeRebuildView", "get_published_resume_snapshot",
    "get_candidate_resume_rebuild_view",
    *_LAZY_EXPORTS.keys(),
]