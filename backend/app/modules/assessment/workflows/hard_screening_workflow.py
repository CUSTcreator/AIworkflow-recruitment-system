"""硬筛 Workflow：冻结来源 → 运行规则 → 发布结论。

每一步都由 StepRunner 包裹；阶段结果保存为 WorkflowArtifact，最终的
HardScreeningResult 与成功检查点在发布步骤的短事务内一起提交。
"""
from __future__ import annotations

from backend.app.db.session import SessionLocal
from backend.app.infrastructure.workflow_runtime.artifact_store import WorkflowArtifactStore
from backend.app.models.entities import WorkflowRun
from backend.app.modules.assessment.hard_screening.hard_screening_service import HardScreeningService
from backend.app.modules.assessment.services.hard_screening_activity_service import HardScreeningActivityService
from backend.app.shared.workflows import ActivityOutcomeKind, RecoveryAction, StepDefinition, StepErrorCategory, StepOutcome, StepPolicy, WorkflowSpec
from recruitment_ai_core.execution import model_call_scope
from recruitment_ai_core.hard_screening import evaluate_hard_screening


WORKFLOW_TYPE = "hard_screening_workflow"


def _input(run_id: str) -> dict:
    """输入哈希只包含任务、申请、策略和当前简历版本的稳定标识。"""
    with SessionLocal() as db:
        run = db.get(WorkflowRun, run_id)
        if run is None:
            raise RuntimeError("workflow_run_not_found")
        payload = dict(run.input_json or {})
        return {
            "applicationId": run.application_id,
            "policyId": payload.get("policy_id"),
            "subjectType": run.subject_type,
            "subjectId": run.subject_id,
        }


def _freeze_handler(context) -> StepOutcome:
    """读取并校验硬筛所需简历、岗位规则和候选人事实，尚不写正式结果。"""
    with SessionLocal() as db:
        payload = HardScreeningService(db).build_workflow_input(context.workflow_run_id, context.worker_id)
    return StepOutcome.succeeded(data=payload)


def _freeze_persist(db, context, outcome) -> dict:
    """短事务：先固化输入工件，再将申请推进为硬筛处理中。"""
    if not isinstance(outcome.data, dict):
        raise RuntimeError("hard_screening_input_missing")
    refs = WorkflowArtifactStore().persist_outcome_json(
        db, workflow_run_id=context.workflow_run_id,
        artifact_type="hard_screening_source_manifest", outcome=outcome,
    )
    run = db.get(WorkflowRun, context.workflow_run_id)
    if run is None:
        raise RuntimeError("workflow_run_not_found")
    HardScreeningService(db).mark_workflow_running(run)
    return refs


def _evaluate_handler(context) -> StepOutcome:
    """事务外运行或复用硬筛活动；语义规则的 LLM 不会因 Step 重试重复调用。"""
    refs = context.previous_output_refs.get("freeze_hard_screen_sources") or {}
    artifact_id = str(refs.get("artifactId") or "")
    with SessionLocal() as db:
        source = WorkflowArtifactStore().get_json(db, artifact_id)
    with model_call_scope(context.external_request_id, "hard_screening_evaluation"):
        batch = HardScreeningActivityService().evaluate(context, dict(source))
    if batch.status in {ActivityOutcomeKind.COMPLETED.value, ActivityOutcomeKind.DEGRADED.value}:
        return StepOutcome.succeeded(data=dict(batch.results["hard_screening_evaluation"]))
    if batch.status == ActivityOutcomeKind.BLOCKED.value:
        return StepOutcome.blocked(
            error_code=batch.error_code or "hard_screening_review_required",
            error_message=batch.error_message or "硬筛输入需要人工确认",
            recovery_action=RecoveryAction.REVIEW_REQUIRED,
        )
    if batch.status == "retry_wait":
        return StepOutcome.activity_retry_wait(
            error_code=batch.error_code or "hard_screening_activity_retry_wait",
            error_message=batch.error_message or "语义硬筛活动等待重试",
            retry_after_seconds=max(1, int(batch.retry_after_seconds or 15)),
        )
    return StepOutcome.failed(
        error_code=batch.error_code or "hard_screening_activity_failed",
        error_message=batch.error_message or "语义硬筛活动失败",
        error_category=batch.error_category or StepErrorCategory.EXTERNAL_PERMANENT,
    )


def _evaluate_persist(db, context, outcome) -> dict:
    if not isinstance(outcome.data, dict):
        raise RuntimeError("hard_screening_result_missing")
    return WorkflowArtifactStore().persist_outcome_json(
        db, workflow_run_id=context.workflow_run_id,
        artifact_type="hard_screening_evaluation", outcome=outcome,
    )


def _publish_handler(context) -> StepOutcome:
    """事务外读取硬筛结果；正式状态写入仍由 persist_success 执行。"""
    refs = context.previous_output_refs.get("evaluate_hard_screening") or {}
    with SessionLocal() as db:
        result = WorkflowArtifactStore().get_json(db, str(refs.get("artifactId") or ""))
    return StepOutcome.succeeded(data={"result": result})


def _publish_persist(db, context, _outcome) -> dict:
    result = dict((_outcome.data or {}).get("result") or {})
    run = db.get(WorkflowRun, context.workflow_run_id)
    if run is None or run.status != "running" or run.lease_owner != context.worker_id:
        raise RuntimeError("workflow_lease_lost")
    HardScreeningService(db).complete_workflow(run=run, result=result)
    return {"applicationId": str(run.application_id or ""), "hardScreeningPublished": "true"}


def build_hard_screening_spec(transition_handler, blocked_handler=None) -> WorkflowSpec:
    """声明硬筛的恢复步骤。

    1. 冻结简历、岗位规则和候选人事实；
    2. 运行纯规则硬筛；
    3. 在短事务中发布正式硬筛结论。
    """
    return WorkflowSpec(
        workflow_type=WORKFLOW_TYPE,
        handler=None,
        transition_handler=transition_handler,
        blocked_handler=blocked_handler,
        definition_version=1,
        steps=(
            # 步骤 1：固定本次硬筛的简历、岗位规则和候选人事实来源。
            StepDefinition("freeze_hard_screen_sources", 1, _freeze_handler, _input, StepPolicy(timeout_seconds=30, max_attempts=1, total_deadline_seconds=120), _freeze_persist, artifact_type="hard_screening_source_manifest"),
            # 步骤 2：执行硬筛规则；仅产生可恢复的中间结论，不直接修改 Application。
            StepDefinition("evaluate_hard_screening", 2, _evaluate_handler, _input, StepPolicy(timeout_seconds=90, max_attempts=2, total_deadline_seconds=300, external=True), _evaluate_persist, artifact_type="hard_screening_evaluation"),
            # 步骤 3：发布 HardScreeningResult，并推进后续招聘状态。
            StepDefinition("publish_hard_screening", 3, _publish_handler, _input, StepPolicy(timeout_seconds=30, max_attempts=2, total_deadline_seconds=120), _publish_persist),
        ),
    )
