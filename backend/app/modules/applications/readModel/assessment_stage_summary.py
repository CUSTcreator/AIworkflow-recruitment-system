"""候选人列表的评估阶段摘要投影。

这里是页面导航合同，不是新的业务状态机：正式结果只认已发布的
ApplicationAssessmentVersion，执行状态只来自当前 WorkflowProcessView。
"""
from __future__ import annotations

from typing import Any

from backend.app.shared.workflows.process_view import WorkflowProcessView


_STAGES = (
    ("v1", "初步筛选", "screening", "view_v1_result", "screening-review"),
    ("v2", "一面后评估", "after_first_interview", "view_v2_result", "hr-second-review"),
    ("v3", "二面后评估", "after_second_interview", "view_v3_result", "final-review"),
)

_ACTIVE_PROCESS_STATUSES = {"queued", "running", "retry_wait", "waiting_external"}
_RECOVERABLE_PROCESS_STATUSES = {"blocked", "failed"}


def assessment_stage_summaries(
    application_id: str,
    versions: dict[str, Any],
    processes: dict[str, WorkflowProcessView | None],
) -> list[dict[str, Any]]:
    """生成固定顺序的 V1/V2/V3 摘要，保证前端无需猜测阶段状态。"""
    result: list[dict[str, Any]] = []
    for stage, label, persisted_stage, action, route_name in _STAGES:
        version = versions.get(persisted_stage)
        process = processes.get(persisted_stage)
        process_status = process.process_status if process is not None else ""
        if process_status in _ACTIVE_PROCESS_STATUSES:
            status = "processing"
        elif process_status in _RECOVERABLE_PROCESS_STATUSES:
            status = "recoverable"
        elif version is not None:
            status = "completed"
        else:
            status = "not_started"
        available = version is not None
        result.append(
            {
                "stage": stage,
                "label": label,
                "status": status,
                "resultAvailable": available,
                "publishedAssessmentVersionId": (
                    str(version.assessment_version_id) if version is not None else None
                ),
                "viewAction": (
                    {
                        "action": action,
                        "label": f"查看{label}",
                        "route": f"/applications/{application_id}/{route_name}",
                    }
                    if available
                    else None
                ),
            }
        )
    return result


def current_execution(
    candidates: list[tuple[str, str, WorkflowProcessView | None]],
) -> dict[str, Any] | None:
    """只选择仍未完成的最新任务，过滤 completed/cancelled 等历史轨迹。"""
    visible = [
        (index, stage, label, process)
        for index, (stage, label, process) in enumerate(candidates)
        if process is not None
        and process.process_status in (_ACTIVE_PROCESS_STATUSES | _RECOVERABLE_PROCESS_STATUSES)
    ]
    if not visible:
        return None
    # 更新时间优先；同一时间按流程顺序取后置阶段，避免旧任务遮挡当前任务。
    _, stage, label, process = max(
        visible,
        key=lambda item: (
            item[3].updated_at.timestamp() if item[3].updated_at is not None else 0.0,
            item[0],
        ),
    )
    return {
        "stage": stage,
        "label": label,
        "process": process.to_public_dict(),
    }
