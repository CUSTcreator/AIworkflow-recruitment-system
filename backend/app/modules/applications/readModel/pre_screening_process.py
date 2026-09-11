"""Application 创建后到 V1 完成前的页面状态投影。

这里不保存业务状态。Application、JobVersion 与 WorkflowRun 仍各自维护唯一事实，
本模块只把这些事实组合成招聘流程页可直接展示的一个状态码和用户文案。
"""
from __future__ import annotations

from typing import Any


_ACTIVE_PROCESS_STATUSES = {"queued", "running", "retry_wait", "waiting_external"}


def _workflow_message(process: Any | None, fallback: str) -> str:
    message = str(getattr(process, "public_message", "") or "").strip()
    return message or fallback


def _view(
    status: str,
    label: str,
    message: str,
    *,
    tone: str,
    workflow_run_id: str | None = None,
) -> dict[str, object]:
    return {
        "status": status,
        "label": label,
        "message": message,
        "tone": tone,
        "workflowRunId": workflow_run_id,
    }


def pre_screening_process_view(
    *,
    application_status: str,
    job_profile_status: str,
    job_profile_available: bool = False,
    job_profile_message: str = "",
    job_profile_workflow_run_id: str | None = None,
    application_recovery_message: str = "",
    hard_screening_process: Any | None = None,
    screening_process: Any | None = None,
) -> dict[str, object] | None:
    """将多个状态拥有者投影为一个页面码，不反向改写任何领域对象。"""

    if application_status == "waiting_job_profile":
        if job_profile_available:
            # 当前有效画像可以继续使用；最新一轮重新生成是否仍在运行不应阻塞申请。
            return _view(
                "initial_assessment_failed",
                "初步筛选启动失败",
                application_recovery_message
                or "岗位画像已就绪，但本申请尚未进入硬筛或初步筛选，可直接重试启动。",
                tone="danger",
            )
        if job_profile_status == "queued":
            return _view(
                "job_profile_queued",
                "岗位画像排队中",
                "岗位画像完成后将自动进入硬筛或初步筛选。",
                tone="info",
                workflow_run_id=job_profile_workflow_run_id,
            )
        if job_profile_status == "processing":
            return _view(
                "job_profile_processing",
                "岗位画像生成中",
                "岗位画像完成后将自动进入硬筛或初步筛选。",
                tone="info",
                workflow_run_id=job_profile_workflow_run_id,
            )
        if job_profile_status == "review_required":
            return _view(
                "job_profile_review_required",
                "岗位画像待确认",
                job_profile_message or "岗位能力画像需要确认后才能继续初步筛选。",
                tone="warning",
                workflow_run_id=job_profile_workflow_run_id,
            )
        if job_profile_status == "failed":
            return _view(
                "job_profile_failed",
                "岗位画像生成失败",
                job_profile_message or "岗位画像未能完成，请到岗位管理处理。",
                tone="danger",
                workflow_run_id=job_profile_workflow_run_id,
            )
        if job_profile_status == "ready":
            # 画像已就绪但 Application 仍在等待态，说明交汇调度没有完整发布。
            return _view(
                "initial_assessment_failed",
                "初步筛选启动失败",
                "岗位画像已就绪，但本申请尚未进入硬筛或初步筛选，可直接重试启动。",
                tone="danger",
            )
        return _view(
            "waiting_job_profile",
            "等待岗位画像",
            "岗位画像完成后将自动进入硬筛或初步筛选。",
            tone="neutral",
            workflow_run_id=job_profile_workflow_run_id,
        )

    if application_status in {"hard_screening_pending", "hard_screening_running"}:
        process_status = str(getattr(hard_screening_process, "process_status", "") or "")
        workflow_run_id = str(getattr(hard_screening_process, "workflow_run_id", "") or "") or None
        if process_status in _ACTIVE_PROCESS_STATUSES:
            label = "硬筛等待中" if process_status == "queued" else "硬筛运行中"
            return _view(
                "hard_screening_queued" if process_status == "queued" else "hard_screening_running",
                label,
                _workflow_message(hard_screening_process, "系统正在执行硬性筛选。"),
                tone="info",
                workflow_run_id=workflow_run_id,
            )
        if process_status in {"failed", "blocked"}:
            return _view(
                "hard_screening_failed",
                "硬筛处理异常",
                _workflow_message(hard_screening_process, "硬筛未能完成，请重试或人工处理。"),
                tone="danger" if process_status == "failed" else "warning",
                workflow_run_id=workflow_run_id,
            )
        return _view(
            "initial_assessment_failed",
            "初步筛选启动失败",
            application_recovery_message or "本申请尚未创建硬筛任务，可直接重试启动。",
            tone="danger",
        )

    if application_status == "hard_screening_review":
        return _view(
            "hard_screening_review_required",
            "硬筛待确认",
            application_recovery_message
            or _workflow_message(hard_screening_process, "请查看硬筛结果并选择重试、通过或拒绝。"),
            tone="warning",
            workflow_run_id=(
                str(getattr(hard_screening_process, "workflow_run_id", "") or "") or None
            ),
        )

    if application_status in {"submitted", "screening_running", "screening_failed"}:
        process_status = str(getattr(screening_process, "process_status", "") or "")
        workflow_run_id = str(getattr(screening_process, "workflow_run_id", "") or "") or None
        if application_status == "screening_failed" or process_status in {"failed", "blocked"}:
            return _view(
                "v1_failed",
                "初步筛选异常",
                application_recovery_message
                or _workflow_message(screening_process, "初步筛选未能完成，可重新运行。"),
                tone="danger" if process_status != "blocked" else "warning",
                workflow_run_id=workflow_run_id,
            )
        if process_status in _ACTIVE_PROCESS_STATUSES:
            label = "初步筛选排队中" if process_status == "queued" else "初步筛选运行中"
            return _view(
                "v1_queued" if process_status == "queued" else "v1_running",
                label,
                _workflow_message(screening_process, "系统正在执行初步筛选评分。"),
                tone="info",
                workflow_run_id=workflow_run_id,
            )
        return _view(
            "v1_scheduling",
            "等待启动初步筛选",
            "本申请尚未创建初步筛选任务，可直接重新启动。",
            tone="warning",
        )

    return None
