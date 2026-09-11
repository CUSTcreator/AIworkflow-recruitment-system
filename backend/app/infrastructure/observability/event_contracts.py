"""执行时间线的稳定合同。

这里的枚举同时约束 Worker、StepRunner、API 和前端；不允许把底层异常文本作为
用户文案透传。这样技术日志可以继续保留诊断信息，而业务页面只展示稳定状态。
"""
from __future__ import annotations

from enum import StrEnum
from typing import Any


class ExecutionEventType(StrEnum):
    STEP_STARTED = "step_started"
    STEP_SUCCEEDED = "step_succeeded"
    STEP_DEFERRED = "step_deferred"
    STEP_BLOCKED = "step_blocked"
    STEP_FAILED = "step_failed"
    WORKFLOW_COMPLETED = "workflow_completed"
    # 用户主动重新计算时，旧任务被终止，后续步骤不得继续发布领域结果。
    WORKFLOW_CANCELLED = "workflow_cancelled"
    # 任务最终失败与步骤失败不同：前者表示领域状态已完成失败收尾。
    WORKFLOW_FAILED = "workflow_failed"
    EXTERNAL_ACTIVITY_STARTED = "external_activity_started"
    EXTERNAL_ACTIVITY_SUCCEEDED = "external_activity_succeeded"
    EXTERNAL_ACTIVITY_FAILED = "external_activity_failed"


class ExecutionSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


_MESSAGES: dict[ExecutionEventType, str] = {
    # 步骤名称由时间线 DTO 的 stepLabel 单独提供。消息不拼接 step_name，避免新增
    # 步骤尚未登记中文名称时把内部编码带到候选人和岗位页面。
    ExecutionEventType.STEP_STARTED: "开始执行当前步骤",
    ExecutionEventType.STEP_SUCCEEDED: "当前步骤已完成",
    ExecutionEventType.STEP_DEFERRED: "当前步骤将在稍后自动继续",
    ExecutionEventType.STEP_BLOCKED: "当前步骤等待确认",
    ExecutionEventType.STEP_FAILED: "当前步骤执行失败",
    ExecutionEventType.WORKFLOW_COMPLETED: "任务已完成",
    ExecutionEventType.WORKFLOW_CANCELLED: "任务已被新的重新计算任务替代",
    ExecutionEventType.WORKFLOW_FAILED: "任务已失败",
    ExecutionEventType.EXTERNAL_ACTIVITY_STARTED: "已提交外部服务请求",
    ExecutionEventType.EXTERNAL_ACTIVITY_SUCCEEDED: "外部服务已返回结果",
    ExecutionEventType.EXTERNAL_ACTIVITY_FAILED: "外部服务调用失败",
}


_RECOVERY_LABELS = {
    "": "",
    "auto_retry": "系统将自动重试",
    "user_retry": "可手动重试",
    "review_required": "等待确认",
    "continue_manually": "等待人工继续",
}

_ERROR_LABELS = {
    "step_attempts_exhausted": "已达到最大重试次数",
    "external_poll_attempts_exhausted": "外部服务长时间未返回",
    "step_deadline_exceeded": "步骤超过处理时限",
    "publish_screening_assessment": "发布初步筛选评估失败",
    "publish_post_interview_assessment": "发布面试后评估失败",
    "publish_resume_result": "发布简历处理结果失败",
}


def recovery_action_label(value: str | None) -> str:
    """把恢复策略枚举转换成业务页面可读的中文。"""
    if not value:
        return ""
    return _RECOVERY_LABELS.get(value, "请按页面提示继续处理")


def error_code_label(value: str | None) -> str:
    """把稳定错误码转换成业务页面可读的中文；未知码不直接暴露。"""
    if not value:
        return ""
    return _ERROR_LABELS.get(value, "系统处理异常")

def public_message(event_type: ExecutionEventType, *, step_name: str | None = None) -> str:
    return _MESSAGES[event_type].format(step_name=step_name or "当前步骤")


def safe_diagnostics(values: dict[str, Any] | None = None) -> dict[str, Any]:
    """只允许结构化且短小的运维字段进入时间线，拒绝异常正文和业务文本。"""
    # 统一 Activity 与数据库适配器的安全诊断字段。这里仍使用已有 JSON
    # 容器承载可选字段，不新增日志表；任何业务正文、Prompt 和 SQL 参数都拒绝写入。
    accepted = {
        "next_attempt_at", "status", "max_attempts", "max_poll_attempts",
        "service", "operation", "external_status", "recovery_action",
        "activity_type", "activity_key", "duration_ms", "retryable", "degraded",
        "external_request_id", "model", "schema_valid", "input_tokens", "output_tokens",
        "response_latency_ms", "fallback_used", "external_job_id", "poll_count",
        "remote_status", "download_status", "bucket", "object_key", "object_sha256",
        "object_size", "http_status", "endpoint", "response_size", "sqlstate",
        "constraint_name", "table_name", "column_name", "statement_fingerprint",
        "transaction_phase",
    }
    source = values or {}
    result: dict[str, Any] = {}
    for key in accepted:
        value = source.get(key)
        if value is None:
            continue
        result[key] = str(value)[:128] if not isinstance(value, (int, float, bool)) else value
    return result
