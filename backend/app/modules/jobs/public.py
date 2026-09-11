"""Job 对外稳定入口。

Candidate 分发只能从这里读取开放且岗位画像已就绪的冻结岗位版本。JobDraft、岗位
导入中间结果和 Job 内部写入 Service 均不对其他模块开放；岗位画像就绪通过稳定
事件载荷通知订阅端。历史管理能力按需加载以避免与 Application 形成循环导入。
"""
from __future__ import annotations

from importlib import import_module

from backend.app.modules.jobs.contracts import (
    JobProfileReadyEvent,
    JobRoutingVersion,
    RoutableJobVersion,
    build_job_profile_ready_event,
    get_routable_job_version,
    list_matchable_job_versions,
    list_open_job_routing_versions,
    list_routable_job_versions,
)
from backend.app.modules.jobs.job_process_service import JobProcessService, JobProfileStatus
from backend.app.modules.jobs.job_recovery import JobRecoveryCode, job_recovery_actions, job_recovery_plan, job_recovery_view
from backend.app.modules.jobs.profile_readiness import (
    JobProfileScreeningReadiness,
    evaluate_job_profile_json,
    evaluate_job_profile_record,
)
from backend.app.modules.jobs.configuration_status import (
    JobAssignmentRole,
    missing_job_assignments,
)

_LAZY_EXPORTS = {
    "JobAccessService": ("backend.app.modules.jobs.access_service", "JobAccessService"),
    "JobLifecycleService": ("backend.app.modules.jobs.lifecycle_service", "JobLifecycleService"),
    "JobProfileService": ("backend.app.modules.jobs.services.job_profile_service", "JobProfileService"),
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
    "JobProcessService", "JobProfileStatus",
    "JobRoutingVersion", "RoutableJobVersion", "JobProfileReadyEvent", "list_matchable_job_versions", "list_open_job_routing_versions", "list_routable_job_versions",
    "get_routable_job_version", "build_job_profile_ready_event", "JobRecoveryCode", "job_recovery_actions", "job_recovery_plan", "job_recovery_view", *_LAZY_EXPORTS.keys(),
    "JobProfileScreeningReadiness", "evaluate_job_profile_json", "evaluate_job_profile_record",
    "JobAssignmentRole", "missing_job_assignments",
]
