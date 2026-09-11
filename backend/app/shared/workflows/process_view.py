"""将 WorkflowRun 与当前检查点投影为稳定的页面流程状态。

业务页面只能读取本模块返回的 ``WorkflowProcessView``，不能自行解析
WorkflowRun.status、检查点状态或异常文本。技术诊断字段通过 ``operatorMessage``
提供给受控的运维界面，普通业务页面只展示 ``publicMessage``。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.entities import WorkflowRun, WorkflowStepCheckpoint
from backend.app.shared.time_serialization import utc_iso
from backend.app.shared.workflows.error_recovery_policy import resolve_recovery
from backend.app.infrastructure.observability.event_contracts import recovery_action_label
from backend.app.shared.workflows.step_contracts import StepErrorCategory, StepOutcomeKind, StepStatus


@dataclass(frozen=True, slots=True)
class WorkflowProcessView:
    """一个 Workflow 在业务页面中的当前可见状态。"""

    workflow_run_id: str = ""
    workflow_type: str = ""
    process_status: str = "not_started"
    current_step: str = ""
    current_step_label: str = ""
    attempt_count: int = 0
    max_attempts: int = 0
    poll_count: int = 0
    max_poll_attempts: int = 0
    next_attempt_at: datetime | None = None
    public_message: str = ""
    operator_message: str = ""
    error_category: str = ""
    error_code: str = ""
    recovery_action: str = ""
    recovery_action_label: str = ""
    updated_at: datetime | None = None

    def to_public_dict(self) -> dict[str, Any]:
        """返回给业务页面的最小状态合同，不泄露诊断信息。"""
        return {
            "workflowRunId": self.workflow_run_id,
            "workflowType": self.workflow_type,
            "processStatus": self.process_status,
            "currentStep": self.current_step,
            "currentStepLabel": self.current_step_label,
            "attemptCount": self.attempt_count,
            "maxAttempts": self.max_attempts,
            "pollCount": self.poll_count,
            "maxPollAttempts": self.max_poll_attempts,
            "nextAttemptAt": utc_iso(self.next_attempt_at),
            "publicMessage": self.public_message,
            "recoveryAction": self.recovery_action,
            "recoveryActionLabel": self.recovery_action_label,
            "updatedAt": utc_iso(self.updated_at),
        }
    def to_dict(self) -> dict[str, Any]:
        """HTTP DTO 统一使用 camelCase。"""
        value = asdict(self)
        return {
            "workflowRunId": value["workflow_run_id"],
            "workflowType": value["workflow_type"],
            "processStatus": value["process_status"],
            "currentStep": value["current_step"],
            "currentStepLabel": value["current_step_label"],
            "attemptCount": value["attempt_count"],
            "maxAttempts": value["max_attempts"],
            "pollCount": value["poll_count"],
            "maxPollAttempts": value["max_poll_attempts"],
            "nextAttemptAt": utc_iso(value["next_attempt_at"]),
            "publicMessage": value["public_message"],
            "operatorMessage": value["operator_message"],
            "errorCategory": value["error_category"],
            "errorCode": value["error_code"],
            "recoveryAction": value["recovery_action"],
            "recoveryActionLabel": value["recovery_action_label"],
            "updatedAt": utc_iso(value["updated_at"]),
        }


_STEP_LABELS = {
    "freeze_job_version": "冻结岗位版本",
    "compile_and_publish_profile": "生成岗位能力画像",
    "mark_job_version_ready": "开放岗位画像",
    "extract_job_document": "提取岗位文档",
    "publish_job_drafts": "发布岗位草稿",
    "freeze_resume_sources": "冻结简历来源",
    "parse_resume_document": "解析简历文件",
    "parse_source_document": "解析简历文件",
    "structure_resume": "结构化简历信息",
    "structure_resume_profile": "结构化简历信息",
    "publish_resume_result": "发布简历处理结果",
    "freeze_routing_sources": "冻结岗位分发来源",
    "match_candidate_jobs": "匹配候选人岗位",
    "publish_applications_and_enqueue_hard_screening": "发布岗位申请",
    "freeze_hard_screen_sources": "冻结硬筛来源",
    "evaluate_hard_screening": "执行硬性筛选",
    "publish_hard_screening": "发布硬筛结果",
    "freeze_screening_sources": "锁定初步筛选依据",
    "freeze_job_profile": "冻结岗位能力画像",
    "build_scoring_input": "组装评分输入",
    "run_screening_core": "计算初步筛选分数",
    "derive_screening_rules": "生成初步筛选结论",
    "generate_screening_presentation": "生成初步筛选展示内容",
    "publish_screening_assessment": "发布初步筛选评估",
    "publish_assessment": "发布评估结果",
    "freeze_first_interview_sources": "冻结一面题单来源",
    "build_question_constraints": "生成题目约束",
    "generate_question_proposals": "生成题目建议",
    "derive_first_interview_rules": "生成题单规则结论",
    "build_first_interview_presentation": "生成题单展示内容",
    "publish_first_interview_plan": "发布一面题单",
    "freeze_post_interview_sources": "冻结面评来源",
    "parse_interview_units": "提取面评语义单元",
    "evaluate_anchor_judgements": "评估锚点判断",
    "run_topology_incremental_scoring": "执行拓扑增量评分",
    "derive_incremental_rules": "生成增量规则结论",
    "generate_incremental_presentation": "生成评估展示内容",
    "publish_post_interview_assessment": "发布面试后评估",
    "route_candidate_jobs": "匹配候选人岗位",
    "publish_applications": "创建岗位申请",
    "freeze_interview_sources": "冻结面试评估来源",
    "parse_interview_record": "解析面试记录",
    "publish_interview_assessment": "发布面试后评估",
}

def workflow_step_label(step_name: str | None) -> str:
    """返回工作流步骤面向业务页面的稳定中文名称。

    未登记的新步骤使用中文通用文案，绝不能把步骤编码加工后直接展示。
    """
    if not step_name:
        return "流程已创建"
    return _STEP_LABELS.get(step_name, "流程处理中")
def _outcome_kind_for_status(status: str) -> StepOutcomeKind | None:
    """将持久化检查点状态还原为恢复策略所需的最小语义。"""
    return {
        "retry_wait": StepOutcomeKind.RETRY_WAIT,
        "activity_retry_wait": StepOutcomeKind.ACTIVITY_RETRY_WAIT,
        "waiting_external": StepOutcomeKind.WAITING_EXTERNAL,
        "blocked": StepOutcomeKind.BLOCKED,
        "failed": StepOutcomeKind.FAILED,
    }.get(status)


def _checkpoint_status(checkpoint: WorkflowStepCheckpoint) -> tuple[str, str, str]:
    """把技术状态收敛为前端的规范流程状态和两类文案。"""
    status = checkpoint.status
    if status in {StepStatus.RETRY_WAIT.value, StepStatus.ACTIVITY_RETRY_WAIT.value}:
        operator_message = (
            "子任务正在等待重试；已完成的子任务不会重复执行。"
            if status == StepStatus.ACTIVITY_RETRY_WAIT.value
            else "请查看错误类别和错误码；无需重复提交任务。"
        )
        return "retry_wait", "当前步骤暂时不可用，系统将自动重试。", operator_message
    if status == "waiting_external":
        return "waiting_external", "正在等待外部服务返回结果。", "外部请求已提交，系统会按计划轮询。"
    if status == "blocked":
        return "blocked", "当前步骤已暂停，请确认或补充信息后继续。", "请按业务页面提示完成确认或补充信息。"
    if status == "failed":
        return "failed", "当前步骤处理失败。", "请通过业务页面提供的恢复操作继续。"
    if status == "running":
        return "running", "当前步骤正在处理。", "Worker 已领取该步骤。"
    if status == "pending":
        return "queued", "当前步骤正在等待执行。", "等待可用 Worker 领取。"
    if status == "succeeded":
        return "completed", "当前流程已完成。", ""
    if status == "cancelled":
        return "cancelled", "当前流程已取消。", ""
    if status == "invalidated":
        return "invalidated", "旧输入已失效，系统将按最新版本重新生成。", "旧步骤产物已失效，不能继续旧任务。"
    return "running", "正在更新处理进度。", ""


def _run_only_view(run: WorkflowRun | None) -> WorkflowProcessView:
    if run is None:
        return WorkflowProcessView()
    status = {
        "pending": "queued",
        "running": "running",
        "blocked": "blocked",
        "failed": "failed",
        "completed": "completed",
        "cancelled": "cancelled",
    }.get(run.status, "not_started")
    message = {
        "queued": "任务正在等待执行。",
        "running": "任务正在处理。",
        "blocked": "任务已暂停，请确认或补充信息后继续。",
        "failed": "任务处理失败。",
        "completed": "任务已完成。",
        "cancelled": "任务已取消。",
    }.get(status, "")
    outcome_kind = StepOutcomeKind.FAILED if status == "failed" else None
    decision = resolve_recovery(
        error_code=None,
        error_category=StepErrorCategory.INTERNAL if status == "failed" else None,
        outcome_kind=outcome_kind,
    )
    return WorkflowProcessView(
        workflow_run_id=run.workflow_run_id,
        workflow_type=run.workflow_type,
        process_status=status,
        public_message=decision.public_message if status == "failed" else message,
        operator_message=run.error_message or "",
        recovery_action=decision.action.value if outcome_kind else "",
        recovery_action_label=recovery_action_label(decision.action.value) if outcome_kind else "",
        updated_at=run.updated_at or run.completed_at or run.started_at,
    )


def load_workflow_processes(db: Session, workflow_run_ids: set[str]) -> dict[str, WorkflowProcessView]:
    """批量读取任务的当前检查点；无检查点的历史任务降级为 WorkflowRun 投影。"""
    if not workflow_run_ids:
        return {}
    runs = {
        row.workflow_run_id: row
        for row in db.scalars(select(WorkflowRun).where(WorkflowRun.workflow_run_id.in_(workflow_run_ids))).all()
    }
    grouped: dict[str, list[WorkflowStepCheckpoint]] = {}
    for row in db.scalars(
        select(WorkflowStepCheckpoint)
        .where(WorkflowStepCheckpoint.workflow_run_id.in_(workflow_run_ids))
        .order_by(WorkflowStepCheckpoint.workflow_run_id, WorkflowStepCheckpoint.step_order)
    ).all():
        grouped.setdefault(row.workflow_run_id, []).append(row)
    result = {run_id: _run_only_view(run) for run_id, run in runs.items()}
    terminal = {"succeeded", "cancelled", "invalidated"}
    for run_id, checkpoints in grouped.items():
        checkpoint = next((item for item in checkpoints if item.status not in terminal), checkpoints[-1])
        process_status, public_message, operator_message = _checkpoint_status(checkpoint)
        decision = resolve_recovery(error_code=checkpoint.last_error_code, error_category=checkpoint.last_error_category, outcome_kind=_outcome_kind_for_status(checkpoint.status))
        if checkpoint.status in {"retry_wait", "activity_retry_wait", "waiting_external", "blocked", "failed"}:
            public_message = decision.public_message
        run = runs.get(run_id)
        result[run_id] = WorkflowProcessView(
            workflow_run_id=run_id,
            workflow_type=run.workflow_type if run is not None else "",
            process_status=process_status,
            current_step=checkpoint.step_name,
            current_step_label=workflow_step_label(checkpoint.step_name),
            attempt_count=checkpoint.attempt_count,
            max_attempts=checkpoint.max_attempts_snapshot,
            poll_count=checkpoint.poll_count,
            max_poll_attempts=checkpoint.max_poll_attempts_snapshot,
            next_attempt_at=checkpoint.next_attempt_at,
            public_message=public_message,
            operator_message=operator_message,
            error_category=checkpoint.last_error_category or "",
            error_code=checkpoint.last_error_code or "",
            recovery_action=decision.action.value,
            recovery_action_label=recovery_action_label(decision.action.value),
            updated_at=checkpoint.updated_at,
        )
    return result


def workflow_process_view(db: Session, run: WorkflowRun | None) -> WorkflowProcessView:
    """单个任务的便利查询；页面读模型优先使用批量函数避免 N+1。"""
    if run is None:
        return WorkflowProcessView()
    return load_workflow_processes(db, {run.workflow_run_id}).get(run.workflow_run_id, _run_only_view(run))






