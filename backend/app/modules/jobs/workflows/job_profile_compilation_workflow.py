"""岗位能力画像生成工作流。

输入：一版冻结的 JobVersionRecord。关键字段为 jd_version_id、source_text、
source_sha256 和预设模型 ID/版本。
输出：同一 JD 版本唯一的 JobRequirementProfileRecord；它是候选人分发和 V1
评分读取的正式岗位能力事实，不能由本工作流创建 Application 或修改 Candidate。
"""
from __future__ import annotations

from backend.app.db.session import SessionLocal
from backend.app.infrastructure.workflow_runtime.artifact_store import WorkflowArtifactStore
from backend.app.modules.jobs.services.job_profile_service import JobProfileService
from backend.app.modules.jobs.services.job_profile_activity_service import (
    JobProfileActivityService,
)
from recruitment_ai_core.execution import model_call_scope

from backend.app.shared.workflows import (
    RecoveryAction,
    StepDefinition,
    StepErrorCategory,
    StepOutcome,
    StepPolicy,
    WorkflowSpec,
)


def _workflow_input(workflow_run_id: str) -> dict:
    """步骤输入哈希只覆盖冻结的版本标识、内容哈希和模型版本。"""
    with SessionLocal() as db:
        return JobProfileService(db).load_input_for_run(
            workflow_run_id
        ).checkpoint_input()


def _start_handler(_context) -> StepOutcome:
    """步骤 1：无外部调用；状态写入由 persist_success 在短事务内完成。"""
    return StepOutcome.succeeded()


def _start_persist(db, context, _outcome) -> dict:
    return JobProfileService(db).mark_processing(context.workflow_run_id)


def _compile_handler(context) -> StepOutcome:
    """步骤 2：按 JDUnit 预算批次恢复 LLM 能力提取，再纯汇总岗位画像。"""
    with SessionLocal() as db:
        service = JobProfileService(db)
        frozen = service.load_input_for_run(context.workflow_run_id)
        ready_retry_profile = service.load_ready_retry_profile(context.workflow_run_id)
    if ready_retry_profile is not None:
        # 画像已持久化时只复用标准画像合同，禁止因就绪发布失败再次调用 LLM。
        return StepOutcome.succeeded(data=ready_retry_profile)
    with model_call_scope(context.external_request_id, "job_profile_compilation"):
        batch = JobProfileActivityService().compile(context, frozen)
    if batch.is_usable:
        return StepOutcome.succeeded(data=batch.results["job_profile"])
    if batch.status == "blocked":
        return StepOutcome.blocked(
            error_code=batch.error_code or "job_profile_review_required",
            error_message=batch.error_message or "岗位能力画像需要人工确认后继续",
            recovery_action=RecoveryAction.REVIEW_REQUIRED,
        )
    if batch.status == "retry_wait":
        return StepOutcome.activity_retry_wait(
            error_code=batch.error_code or "job_profile_activity_retry_wait",
            error_message=batch.error_message or "岗位能力提取活动等待重试",
            retry_after_seconds=max(1, int(batch.retry_after_seconds or 15)),
        )
    return StepOutcome.failed(
        error_code=batch.error_code or "job_profile_activity_failed",
        error_message=batch.error_message or "岗位能力提取活动失败",
        error_category=batch.error_category or StepErrorCategory.EXTERNAL_PERMANENT,
    )


def _compile_persist(db, context, outcome) -> dict:
    """步骤 2 后置：正式画像、阶段工件与检查点成功状态在一个短事务中提交。"""
    if not isinstance(outcome.data, dict):
        raise RuntimeError("compiled_job_profile_result_missing")
    # StepRunner 已在 handler 阶段完成大对象暂存；这里仅登记引用，禁止再次外部 I/O。
    artifact_refs = WorkflowArtifactStore().persist_outcome_json(
        db,
        workflow_run_id=context.workflow_run_id,
        artifact_type="job_requirement_profile_draft",
        outcome=outcome,
    )
    profile_refs = JobProfileService(db).publish(context.workflow_run_id, outcome.data)
    return {**profile_refs, **artifact_refs}


def _ready_handler(_context) -> StepOutcome:
    """步骤 3：无外部调用；只在画像已发布后开放该 JD 版本给下游使用。"""
    return StepOutcome.succeeded()


def _ready_persist(db, context, _outcome) -> dict:
    return JobProfileService(db).mark_ready(context.workflow_run_id)


def build_job_profile_compilation_spec(transition_handler, blocked_handler=None) -> WorkflowSpec:
    """声明岗位画像编译的恢复步骤。

    1. 冻结已确认的 JD 版本；
    2. 编译并发布岗位能力画像；
    3. 将该 JD 版本开放给候选人分发和评分流程。
    """
    return WorkflowSpec(
        workflow_type="job_profile_compilation_workflow",
        handler=None,
        transition_handler=transition_handler,
        blocked_handler=blocked_handler,
        definition_version=1,
        steps=(
            # 步骤 1：锁定 JobVersionRecord 的内容哈希和模型版本。
            StepDefinition(
                name="freeze_job_version",
                order=1,
                input_factory=_workflow_input,
                handler=_start_handler,
                persist_success=_start_persist,
                policy=StepPolicy(timeout_seconds=15, max_attempts=1, total_deadline_seconds=60),
            ),
            # 步骤 2：事务外生成画像；成功后在短事务中发布 JobRequirementProfile。
            StepDefinition(
                name="compile_and_publish_profile",
                order=2,
                input_factory=_workflow_input,
                handler=_compile_handler,
                persist_success=_compile_persist,
                artifact_type="job_requirement_profile_draft",
                policy=StepPolicy(timeout_seconds=180, max_attempts=2, total_deadline_seconds=900, initial_backoff_seconds=10, max_backoff_seconds=90, external=True),
            ),
            # 步骤 3：岗位画像已发布，标记这版 JD 可供下游工作流使用。
            StepDefinition(
                name="mark_job_version_ready",
                order=3,
                input_factory=_workflow_input,
                handler=_ready_handler,
                persist_success=_ready_persist,
                policy=StepPolicy(timeout_seconds=15, max_attempts=2, total_deadline_seconds=60),
            ),
        ),
    )
