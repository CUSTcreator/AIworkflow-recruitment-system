"""初筛评分工作流。

工作流只负责编排业务阶段；评分、规则、展示和发布的内部实现分别属于对应模块。
目标顺序固定为：冻结输入 → 纯评分核心 → 规则派生 → 展示生成 → V1 原子发布。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime

from sqlalchemy import select

from backend.app.db.session import SessionLocal
from backend.app.infrastructure.workflow_runtime.artifact_store import WorkflowArtifactStore
from backend.app.models.entities import (
    Application,
    HardScreeningResult,
    Job,
    JobRequirementProfileRecord,
    JobVersionRecord,
    ResumeProfileRecord,
    ResumeSubmission,
    UniversityRankingEntry,
    User,
    WorkflowRun,
)
from backend.app.modules.assessment.services.assessment_version_publisher import (
    AssessmentVersionPublisher,
)
from backend.app.modules.assessment.services.presentation_generation_service import (
    PresentationGenerationService,
)
from backend.app.modules.assessment.services.rule_derivation_service import (
    RuleDerivationService,
)
from backend.app.modules.assessment.services.screening_activity_service import (
    ScreeningActivityService,
)
from backend.app.modules.assessment.services.scoring_source_projection import (
    project_scoring_job_profile,
    project_scoring_resume_profile,
)
from backend.app.modules.assessment.services.screening_source_readiness_service import (
    ScreeningSourceReadiness,
    ScreeningSourceReadinessService,
    ScreeningSourceReadinessStatus,
)
from backend.app.shared.workflows import (
    ActivityOutcomeKind,
    RecoveryAction,
    StepDefinition,
    StepErrorCategory,
    StepOutcome,
    StepPolicy,
    WorkflowSpec,
)
from recruitment_ai_core.execution import model_call_scope
from recruitment_ai_core.screening_scoring.pipeline import assemble_screening_core
from recruitment_ai_core.screening_scoring.result_contracts import (
    PresentationResult,
    RuleDerivedResult,
    ScoringCoreInput,
    ScreeningCoreResult,
)


RANKING_DATASET_VERSION = "softke_bcur_2026"


@dataclass(frozen=True, slots=True)
class FrozenScreeningContext:
    """工作流持有的业务上下文；业务 ID 不进入纯评分核心输入。"""

    workflow_run_id: str
    worker_id: str
    application_id: str
    user_id: str
    core_input: ScoringCoreInput
    hard_screening_result: dict


@dataclass(frozen=True, slots=True)
class FrozenScreeningLoadResult:
    """冻结步骤的明确结果：预期来源问题不再依赖裸异常表达。"""

    readiness: ScreeningSourceReadiness
    frozen: FrozenScreeningContext | None = None


def _load_frozen_screening_context(
    run_id: str,
    worker_id: str,
) -> FrozenScreeningLoadResult:
    """加载 V1 冻结来源，并将预期问题映射为稳定的就绪性结论。

    Worker 租约丢失仍属于运行时并发异常；简历/岗位来源未就绪、部署数据集缺失
    和版本关联错配则由 ``ScreeningSourceReadinessService`` 显式分类，供 StepRunner
    持久化为 blocked 或带恢复动作的失败状态。
    """
    readiness_service = ScreeningSourceReadinessService()
    with SessionLocal() as db:
        run = db.get(WorkflowRun, run_id)
        if run is None or run.status != "running" or run.lease_owner != worker_id:
            raise RuntimeError("workflow_lease_lost")
        # 下游来源修复不改变 Application 主状态。仅该显式标记的 V1 重建任务
        # 可以在 V2/V3 审核阶段重新冻结来源，普通初筛仍必须处于 screening_running。
        allow_downstream_rebuild = bool(
            (run.input_json or {}).get("rebuild_published_assessment")
        )

        application = db.get(Application, run.application_id) if run.application_id else None
        if application is not None and application.deleted_at is not None:
            application = None
        job = db.get(Job, application.job_id) if application and application.job_id else None
        submission = (
            db.get(ResumeSubmission, application.adopted_resume_submission_id)
            if application and application.adopted_resume_submission_id else None
        )
        resume_profile_id = (
            application.source_resume_profile_id
            if application else None
        ) or (submission.output_resume_profile_id if submission else None)
        resume_profile = db.get(ResumeProfileRecord, resume_profile_id) if resume_profile_id else None
        job_profile = (
            db.get(JobRequirementProfileRecord, application.job_profile_id)
            if application and application.job_profile_id else None
        )
        job_version = (
            db.get(JobVersionRecord, application.jd_version_id)
            if application and application.jd_version_id else None
        )

        # 第一轮只检查关系型来源。候选人经历为空属于合法稀疏输入，绝不在此阻塞。
        precheck = readiness_service.assess(
            application=application,
            job=job,
            resume_profile=resume_profile,
            job_profile=job_profile,
            job_version=job_version,
            allow_downstream_rebuild=allow_downstream_rebuild,
        )
        if not precheck.is_ready:
            return FrozenScreeningLoadResult(readiness=precheck)

        try:
            assert resume_profile is not None and job_profile is not None and job_version is not None
            projected_resume_profile = project_scoring_resume_profile(resume_profile)
            projected_job_profile = project_scoring_job_profile(job_profile, job_version)
        except Exception as error:
            decision = readiness_service.assess(
                application=application,
                job=job,
                resume_profile=resume_profile,
                job_profile=job_profile,
                job_version=job_version,
                projection_error=error,
                allow_downstream_rebuild=allow_downstream_rebuild,
            )
            return FrozenScreeningLoadResult(readiness=decision)

        ranking_entries = [
            {
                "canonical_name": item.canonical_name,
                "aliases": list((item.aliases or {}).get("items", [])),
                "rank": item.rank,
            }
            for item in db.scalars(
                select(UniversityRankingEntry)
                .where(UniversityRankingEntry.dataset_version == RANKING_DATASET_VERSION)
                .order_by(UniversityRankingEntry.rank)
            ).all()
        ]
        decision = readiness_service.assess(
            application=application,
            job=job,
            resume_profile=resume_profile,
            job_profile=job_profile,
            job_version=job_version,
            ranking_entries=ranking_entries,
            expected_ranking_count=200,
            projected_resume_profile=projected_resume_profile,
            projected_job_profile=projected_job_profile,
            allow_downstream_rebuild=allow_downstream_rebuild,
        )
        if not decision.is_ready:
            return FrozenScreeningLoadResult(readiness=decision)

        stored_hard_result = db.scalar(
            select(HardScreeningResult)
            .where(HardScreeningResult.application_id == application.application_id)
            .order_by(HardScreeningResult.created_at.desc())
        )
        core_input = ScoringCoreInput(
            resume_profile=projected_resume_profile,
            job_profile=projected_job_profile,
            education_ranking_entries=ranking_entries,
            ranking_dataset_version=RANKING_DATASET_VERSION,
        )
        return FrozenScreeningLoadResult(
            readiness=decision,
            frozen=FrozenScreeningContext(
                workflow_run_id=run.workflow_run_id,
                worker_id=worker_id,
                application_id=application.application_id,
                user_id=run.triggered_by,
                core_input=core_input,
                hard_screening_result=_hard_screening_payload(stored_hard_result),
            ),
        )

def _hard_screening_payload(result: HardScreeningResult | None) -> dict:
    """将持久化硬筛结果转换为规则层的冻结输入形状。"""
    if result is None:
        return {
            "gate": "verified",
            "status": "not_configured",
            "items": [],
            "reason": "岗位未配置硬性筛选条件。",
        }
    rule_results = list(result.rule_results_json or [])
    return {
        "resultId": result.result_id,
        "policyId": result.policy_id,
        "gate": (
            "verified" if result.status == "passed"
            else "not_qualified" if result.status == "failed"
            else "unclear"
        ),
        "status": result.status,
        "items": rule_results,
        "reason": str(result.summary or ""),
    }


# 以下 StepDefinition 是运行时唯一使用的 V1 编排；上方函数只负责冻结输入和
# 复用活动产物，不包含另一套旧流程。
def _step_input(run_id: str) -> dict:
    with SessionLocal() as db:
        run = db.get(WorkflowRun, run_id)
        if run is None:
            raise RuntimeError("workflow_run_not_found")
        return {"applicationId": run.application_id, "triggeredBy": run.triggered_by, "rankingDatasetVersion": RANKING_DATASET_VERSION}


def _freeze_step(context) -> StepOutcome:
    loaded = _load_frozen_screening_context(context.workflow_run_id, context.worker_id)
    if not loaded.readiness.is_ready:
        decision = loaded.readiness
        if decision.status == ScreeningSourceReadinessStatus.BLOCKED:
            return StepOutcome.blocked(
                error_code=decision.error_code,
                error_message=decision.message,
                error_category=decision.error_category or StepErrorCategory.BUSINESS_RULE,
                recovery_action=decision.recovery_action,
            )
        return StepOutcome.failed(
            error_code=decision.error_code,
            error_message=decision.message,
            error_category=decision.error_category or StepErrorCategory.INTERNAL,
            recovery_action=decision.recovery_action,
        )
    frozen = loaded.frozen
    assert frozen is not None
    return StepOutcome.succeeded(data={
        "applicationId": frozen.application_id,
        "userId": frozen.user_id,
        "coreInput": asdict(frozen.core_input),
        "hardScreeningResult": frozen.hard_screening_result,
        "sourceDegraded": bool(loaded.readiness.degraded),
        "sourceWarning": loaded.readiness.message if loaded.readiness.degraded else "",
    })


def _artifact(context, name: str) -> dict:
    refs = context.previous_output_refs.get(name) or {}
    with SessionLocal() as db:
        return WorkflowArtifactStore().get_json(db, str(refs.get("artifactId") or ""))


def _presentation_result_from_artifact(value: dict) -> PresentationResult:
    """Project an Activity artifact onto the strict V1 presentation contract.

    Activity-level quality metadata (for example ``degraded`` and
    ``activityQuality``) is intentionally kept in the runtime/checkpoint layer.
    It must not be passed to ``PresentationResult``, whose fields are the
    persisted business result.  Required contract fields are still validated by
    the dataclass constructor after this projection.
    """
    allowed = {item.name for item in fields(PresentationResult)}
    return PresentationResult(**{
        key: item for key, item in dict(value or {}).items() if key in allowed
    })


def _persist_artifact(kind: str):
    def persist(db, context, outcome):
        return WorkflowArtifactStore().persist_outcome_json(
            db, workflow_run_id=context.workflow_run_id,
            artifact_type=kind, outcome=outcome,
        )
    return persist


def _activity_outcome(batch, result_key: str) -> StepOutcome:
    """将活动级恢复结果映射为 Step 合同，不让子活动重试耗尽整个步骤。"""
    if batch.status in {
        ActivityOutcomeKind.COMPLETED.value,
        ActivityOutcomeKind.DEGRADED.value,
    }:
        data = dict(batch.results[result_key])
        if batch.status == ActivityOutcomeKind.DEGRADED.value:
            data.setdefault("degraded", True)
            if batch.quality_summaries:
                data.setdefault("activityQuality", dict(batch.quality_summaries))
        return StepOutcome.succeeded(data=data)
    if batch.status == ActivityOutcomeKind.BLOCKED.value:
        return StepOutcome.blocked(
            error_code=str(batch.error_code or "screening_activity_blocked"),
            error_message=str(batch.error_message or "初步筛选评分缺少可用输入，等待处理"),
            recovery_action=RecoveryAction.REVIEW_REQUIRED,
        )
    if batch.status == "retry_wait":
        return StepOutcome.activity_retry_wait(
            error_code=str(batch.error_code or "screening_activity_retry_wait"),
            error_message=str(batch.error_message or "初步筛选外部活动等待重试"),
            retry_after_seconds=max(1, int(batch.retry_after_seconds or 15)),
        )
    return StepOutcome.failed(
        error_code=str(batch.error_code or "screening_activity_failed"),
        error_message=str(batch.error_message or "初步筛选外部活动已失败"),
        error_category=batch.error_category or StepErrorCategory.EXTERNAL_PERMANENT,
    )


def _resume_experience_step(context) -> StepOutcome:
    """逐段执行并恢复简历经历评分，已成功段从活动 Artifact 复用。"""
    source = _artifact(context, "freeze_screening_sources")
    with model_call_scope(context.external_request_id, "screening_resume_experience"):
        batch = ScreeningActivityService().score_resume_experience(
            context, ScoringCoreInput(**dict(source["coreInput"]))
        )
    return _activity_outcome(batch, "resume_experience")


def _job_capability_step(context) -> StepOutcome:
    """以已完成经历结果计算岗位能力匹配，避免失败后重新调用经历 LLM。"""
    source = _artifact(context, "freeze_screening_sources")
    experience = _artifact(context, "score_resume_evidence")
    with model_call_scope(context.external_request_id, "screening_job_capability"):
        batch = ScreeningActivityService().score_job_capabilities(
            context,
            ScoringCoreInput(**dict(source["coreInput"])),
            experience,
        )
    return _activity_outcome(batch, "job_capability")


def _assemble_core_step(context) -> StepOutcome:
    """只汇总已持久化的活动产物，形成后续规则层使用的原始评分核心。"""
    source = _artifact(context, "freeze_screening_sources")
    experience = _artifact(context, "score_resume_evidence")
    job_result = _artifact(context, "score_job_capabilities")
    core = assemble_screening_core(
        ScoringCoreInput(**dict(source["coreInput"])), experience, job_result
    )
    return StepOutcome.succeeded(data=asdict(core))

def _rule_step(context) -> StepOutcome:
    source = _artifact(context, "freeze_screening_sources")
    core_data = _artifact(context, "assemble_screening_core")
    core = ScreeningCoreResult(**core_data)
    rule = RuleDerivationService(application_id=str(source["applicationId"])).derive_screening(
        core=core, hard_screening_result=dict(source["hardScreeningResult"])
    )
    return StepOutcome.succeeded(data=asdict(rule))


def _presentation_step(context) -> StepOutcome:
    """恢复单个展示 Bundle Activity，再纯组装前端展示字段。"""
    core = ScreeningCoreResult(**_artifact(context, "assemble_screening_core"))
    rule = RuleDerivedResult(**_artifact(context, "derive_screening_rules"))
    with model_call_scope(context.external_request_id, "screening_presentation"):
        batch = ScreeningActivityService().generate_presentation(
            context, rule=rule, core=core, evidence_index=core.evidence_index
        )
    return _activity_outcome(batch, "presentation")


def _publish_step(context) -> StepOutcome:
    """在事务外准备初筛审计分析包；正式版本仍由 persist_success 原子发布。"""
    source = _artifact(context, "freeze_screening_sources")
    core = ScreeningCoreResult(**_artifact(context, "assemble_screening_core"))
    rule = RuleDerivedResult(**_artifact(context, "derive_screening_rules"))
    presentation = _presentation_result_from_artifact(
        _artifact(context, "generate_screening_presentation")
    )
    return StepOutcome.succeeded(data={
        "source": source,
        "core": asdict(core),
        "rule": asdict(rule),
        "presentation": asdict(presentation),
        "analysisBundle": AssessmentVersionPublisher.stage_screening_analysis_bundle(
            application_id=str(source["applicationId"]),
            workflow_run_id=context.workflow_run_id,
            core=core, rule=rule, presentation=presentation,
            hard_screening_source=dict(source.get("hardScreeningResult") or {}),
        ),
    })


def _publish_step_persist(db, context, _outcome) -> dict:
    prepared = dict(_outcome.data or {})
    source = dict(prepared.get("source") or {})
    core = ScreeningCoreResult(**dict(prepared.get("core") or {}))
    rule = RuleDerivedResult(**dict(prepared.get("rule") or {}))
    presentation = _presentation_result_from_artifact(
        dict(prepared.get("presentation") or {})
    )
    run = db.get(WorkflowRun, context.workflow_run_id)
    app = db.get(Application, str(source["applicationId"])) if run else None
    user = db.get(User, str(source["userId"])) if run else None
    if run is None or run.status != "running" or run.lease_owner != context.worker_id or app is None or user is None:
        raise RuntimeError("workflow_publish_context_changed")
    analysis_bundle = dict((_outcome.data or {}).get("analysisBundle") or {})
    assessment = AssessmentVersionPublisher(db).publish_screening(
        user, app, core=core, rule=rule, presentation=presentation,
        workflow_run=run, analysis_bundle_ref=analysis_bundle,
        hard_screening_source=dict(source.get("hardScreeningResult") or {}),
    )
    return {"assessmentVersionId": assessment.assessment_version_id, "applicationId": app.application_id}


def build_scoring_spec(transition_handler, blocked_handler=None) -> WorkflowSpec:
    """声明初筛 V1 的活动级恢复步骤。

    1. 冻结输入；
    2. 逐段评分简历经历（每段一个活动检查点）；
    3. 评分岗位能力（独立活动检查点）；
    4. 汇总纯评分核心；
    5. 规则派生、展示生成与 V1 原子发布。
    """
    return WorkflowSpec(
        workflow_type="scoring_workflow",
        handler=None,
        transition_handler=transition_handler,
        blocked_handler=blocked_handler,
        definition_version=2,
        steps=(
            StepDefinition("freeze_screening_sources", 1, _freeze_step, _step_input, StepPolicy(timeout_seconds=45, max_attempts=2, total_deadline_seconds=180), _persist_artifact("screening_source_manifest"), artifact_type="screening_source_manifest"),
            # 活动内部最多各自重试三次；该 Step 只负责恢复活动账本，不能重跑成功经历。
            StepDefinition("score_resume_evidence", 2, _resume_experience_step, _step_input, StepPolicy(timeout_seconds=180, max_attempts=2, total_deadline_seconds=1800, external=True), _persist_artifact("screening_resume_experience_result"), artifact_type="screening_resume_experience_result"),
            StepDefinition("score_job_capabilities", 3, _job_capability_step, _step_input, StepPolicy(timeout_seconds=180, max_attempts=2, total_deadline_seconds=900, external=True), _persist_artifact("screening_job_capability_result"), artifact_type="screening_job_capability_result"),
            StepDefinition("assemble_screening_core", 4, _assemble_core_step, _step_input, StepPolicy(timeout_seconds=60, max_attempts=2, total_deadline_seconds=240), _persist_artifact("screening_core_result"), artifact_type="screening_core_result"),
            StepDefinition("derive_screening_rules", 5, _rule_step, _step_input, StepPolicy(timeout_seconds=60, max_attempts=2, total_deadline_seconds=240), _persist_artifact("screening_rule_result"), artifact_type="screening_rule_result"),
            StepDefinition("generate_screening_presentation", 6, _presentation_step, _step_input, StepPolicy(timeout_seconds=180, max_attempts=3, total_deadline_seconds=900, external=True), _persist_artifact("screening_presentation_result"), artifact_type="screening_presentation_result"),
            StepDefinition("publish_screening_assessment", 7, _publish_step, _step_input, StepPolicy(timeout_seconds=90, max_attempts=2, total_deadline_seconds=300), _publish_step_persist),
        ),
    )
