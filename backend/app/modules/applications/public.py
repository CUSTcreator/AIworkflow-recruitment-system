"""Application 对外稳定入口。

本模块拥有 Candidate 对 Job 的招聘主流程状态。Candidate 路由和简历重建只能通过
本文件的冻结命令创建 Application 或更新其采用的 ResumeProfile；禁止直接修改
``Application.status``。契约 DTO 立即导出，旧能力按需加载以避免模块循环依赖。
"""
from __future__ import annotations

from importlib import import_module

from backend.app.modules.applications.application_process_service import ApplicationProcessService, ApplicationTransition
from backend.app.modules.applications.contracts import (
    ApplicationCreationResult,
    CreateApplicationFromRouting,
    ResumeRebuildRequest,
    ResumeRebuildResult,
    apply_candidate_resume_rebuild,
    create_application_from_routing,
)

_LAZY_EXPORTS = {
    "ApplicationAccessService": ("backend.app.modules.applications.services.application_access_service", "ApplicationAccessService"),
    "ApplicationCommandExecutor": ("backend.app.modules.applications.commands.application_command_executor", "ApplicationCommandExecutor"),
    "ApplicationIntakeCommands": ("backend.app.modules.applications.commands.application_intake_commands", "ApplicationIntakeCommands"),
    "ApplicationReadModelSource": ("backend.app.modules.applications.queries.read_model_source", "ApplicationReadModelSource"),
    "ApplicationReadSource": ("backend.app.modules.applications.queries.read_model_source", "ApplicationReadSource"),
    "application_main_route": ("backend.app.modules.applications.domain.application_navigation_policy", "application_main_route"),
    "application_primary_action": ("backend.app.modules.applications.domain.application_navigation_policy", "application_primary_action"),
    "allowed_application_actions": ("backend.app.modules.applications.readModel.page_actions", "allowed_application_actions"),
    "application_recovery_actions": ("backend.app.modules.applications.readModel.page_actions", "application_recovery_actions"),
    "application_recovery_plan": ("backend.app.modules.applications.application_recovery", "application_recovery_plan"),
    "application_view": ("backend.app.modules.applications.readModel.presenters", "application_view"),
    "job_view": ("backend.app.modules.applications.readModel.presenters", "job_view"),
    "ApplicationCommandResponse": ("backend.app.modules.applications.schemas.command_schemas", "ApplicationCommandResponse"),
    "EmptyActionRequest": ("backend.app.modules.applications.schemas.command_schemas", "EmptyActionRequest"),
    "ApplicationView": ("backend.app.modules.applications.schemas.view_schemas", "ApplicationView"),
    "CandidateView": ("backend.app.modules.applications.schemas.view_schemas", "CandidateView"),
    "JobView": ("backend.app.modules.applications.schemas.view_schemas", "JobView"),
    "InvalidTransition": ("backend.app.modules.applications.domain.application_state_machine", "InvalidTransition"),
    "next_status": ("backend.app.modules.applications.domain.application_state_machine", "next_status"),
    "blocks_owner_deletion": ("backend.app.modules.applications.domain.application_deletion_policy", "blocks_owner_deletion"),
    "can_cancel_for_owner_deletion": ("backend.app.modules.applications.domain.application_deletion_policy", "can_cancel_for_owner_deletion"),
    "can_replace_application": ("backend.app.modules.applications.domain.application_lifecycle_policy", "can_replace_application"),
    "is_recruitment_in_progress": ("backend.app.modules.applications.domain.application_lifecycle_policy", "is_recruitment_in_progress"),
    "ApplicationLifecycleCommands": ("backend.app.modules.applications.commands.application_lifecycle_commands", "ApplicationLifecycleCommands"),
    "release_applications_waiting_for_job_profile": ("backend.app.modules.applications.contracts", "release_applications_waiting_for_job_profile"),
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
    "ApplicationProcessService", "ApplicationTransition", "CreateApplicationFromRouting",
    "ApplicationCreationResult", "ResumeRebuildRequest", "ResumeRebuildResult",
    "create_application_from_routing", "apply_candidate_resume_rebuild", *_LAZY_EXPORTS.keys(),
]
