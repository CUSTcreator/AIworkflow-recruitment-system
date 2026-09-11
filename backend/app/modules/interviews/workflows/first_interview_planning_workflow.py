"""一面题单规划 Workflow。

唯一职责是编排“冻结输入 → 约束集 → LLM 提案 → 规则校验 → 一次事务发布”。
每个服务只能处理本步骤的能力，禁止绕过本文件直接改变流程顺序。
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from backend.app.db.session import SessionLocal
from backend.app.infrastructure.workflow_runtime.artifact_store import WorkflowArtifactStore
from backend.app.models.entities import Application, ApplicationAssessmentVersion, User, WorkflowRun
from backend.app.modules.auth.public import assert_permission
from backend.app.modules.interviews.services.first_interview_plan_presentation_service import (
    FirstInterviewPlanPresentationService,
)
from backend.app.modules.interviews.services.first_interview_plan_version_publisher import (
    FirstInterviewPlanVersionPublisher,
)
from backend.app.modules.interviews.services.first_interview_planning_input_service import (
    FirstInterviewPlanningInputService,
)
from backend.app.modules.interviews.services.first_interview_question_activity_service import (
    FirstInterviewQuestionActivityService,
)
from recruitment_ai_core.first_interview_planning.rule_derivation import FirstInterviewRuleDerivationService
from backend.app.shared.errors import BusinessError
from backend.app.shared.workflows import ActivityOutcomeKind, RecoveryAction, StepDefinition, StepErrorCategory, StepOutcome, StepPolicy, WorkflowSpec
from recruitment_ai_core.first_interview_planning import (
    FirstInterviewPlanPresentationResult, QuestionDraftProposal, QuestionPlanningConstraintSet,
    QuestionProposalGenerationResult, QuestionRuleDerivationResult, build_question_planning_constraints,
)
from recruitment_ai_core.first_interview_planning.contracts import FirstInterviewPlanningConfig, FirstInterviewPlanningInput
from recruitment_ai_core.execution import model_call_scope


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# StepRunner 使用的正式一面题单编排：每一层产物先持久化，再运行下一层。
def _step_input(run_id: str) -> dict[str, Any]:
    with SessionLocal() as db:
        run = db.get(WorkflowRun, run_id)
        if run is None:
            raise RuntimeError("workflow_run_not_found")
        return {"applicationId": run.application_id, "action": (run.input_json or {}).get("action"), "body": (run.input_json or {}).get("body") or {}}


def _planning_artifact(context, step: str) -> dict[str, Any]:
    refs = context.previous_output_refs.get(step) or {}
    with SessionLocal() as db:
        return WorkflowArtifactStore().get_json(db, str(refs.get("artifactId") or ""))


def _planning_persist(kind: str):
    def persist(db, context, outcome):
        return WorkflowArtifactStore().persist_outcome_json(
            db, workflow_run_id=context.workflow_run_id,
            artifact_type=kind, outcome=outcome,
        )
    return persist


def _planning_input_from(data: dict[str, Any]) -> FirstInterviewPlanningInput:
    raw = dict(data["planningInput"])
    raw["generation_config"] = FirstInterviewPlanningConfig(**dict(raw.get("generation_config") or {}))
    return FirstInterviewPlanningInput(**raw)


def _freeze_planning_step(context) -> StepOutcome:
    with SessionLocal() as db:
        run = db.get(WorkflowRun, context.workflow_run_id)
        if run is None or run.status != "running" or run.lease_owner != context.worker_id:
            raise RuntimeError("workflow_lease_lost")
        app, user = db.get(Application, run.application_id), db.get(User, run.triggered_by)
        if app is None or app.deleted_at is not None or user is None:
            raise RuntimeError("first_interview_planning_context_missing")
        body = (run.input_json or {}).get("body") if isinstance((run.input_json or {}).get("body"), dict) else {}
        if str((run.input_json or {}).get("action") or "") != "run_first_interview_planning":
            raise RuntimeError("unsupported_workflow_action:first_interview_planning_workflow")
        assert_permission(db, user, app, "run_first_interview_planning")
        try:
            planning_input, source, template = FirstInterviewPlanningInputService(db).load(app, body)
        except BusinessError as error:
            # 冻结来源失败通常需要用户修正业务输入，不应伪装成可自动恢复的技术失败。
            return StepOutcome.blocked(
                error_code=error.code,
                error_message=error.message,
                recovery_action=RecoveryAction.REVIEW_REQUIRED,
            )
        return StepOutcome.succeeded(data={"applicationId": app.application_id, "userId": user.user_id, "sourceAssessmentVersionId": source.assessment_version_id, "template": template or {}, "planningInput": asdict(planning_input)})


def _constraints_step(context) -> StepOutcome:
    source = _planning_artifact(context, "freeze_first_interview_sources")
    constraints = build_question_planning_constraints(_planning_input_from(source))
    return StepOutcome.succeeded(data=asdict(constraints))


def _proposal_step(context) -> StepOutcome:
    """步骤 3：题目提案通过活动检查点执行，避免重试时重复消耗 LLM 调用。"""
    source = _planning_artifact(context, "freeze_first_interview_sources")
    constraints = QuestionPlanningConstraintSet(**_planning_artifact(context, "build_question_constraints"))
    with model_call_scope(context.external_request_id, "first_interview_question_proposals"):
        batch = FirstInterviewQuestionActivityService().generate(
            context,
            planning_input=_planning_input_from(source),
            constraints=constraints,
        )
    if batch.status in {ActivityOutcomeKind.COMPLETED.value, ActivityOutcomeKind.DEGRADED.value}:
        return StepOutcome.succeeded(data=dict(batch.results["question_proposals"]))
    if batch.status == "retry_wait":
        return StepOutcome.activity_retry_wait(
            error_code=batch.error_code or "question_proposal_activity_retry_wait",
            error_message=batch.error_message or "题目提案活动等待重试",
            retry_after_seconds=max(1, int(batch.retry_after_seconds or 15)),
        )
    if batch.status == ActivityOutcomeKind.BLOCKED.value:
        return StepOutcome.blocked(
            error_code=batch.error_code or "question_proposal_activity_blocked",
            error_message=batch.error_message or "题目提案待确认",
            recovery_action=RecoveryAction.CONTINUE_MANUALLY,
        )
    return StepOutcome.failed(
        error_code=batch.error_code or "question_proposal_activity_failed",
        error_message=batch.error_message or "题目提案活动失败",
        error_category=batch.error_category or StepErrorCategory.EXTERNAL_PERMANENT,
    )


def _rule_step(context) -> StepOutcome:
    source = _planning_artifact(context, "freeze_first_interview_sources")
    constraints = QuestionPlanningConstraintSet(**_planning_artifact(context, "build_question_constraints"))
    proposal_data = _planning_artifact(context, "generate_question_proposals")
    proposal = QuestionProposalGenerationResult(proposals=[QuestionDraftProposal(**row) for row in proposal_data.get("proposals") or []], generation_mode=proposal_data["generation_mode"], generation_warnings=list(proposal_data.get("generation_warnings") or []), llm_trace=proposal_data.get("llm_trace"))
    rule = FirstInterviewRuleDerivationService().derive(planning_input=_planning_input_from(source), constraints=constraints, proposal_result=proposal)
    return StepOutcome.succeeded(data=asdict(rule))


def _presentation_step(context) -> StepOutcome:
    source = _planning_artifact(context, "freeze_first_interview_sources")
    constraints = QuestionPlanningConstraintSet(**_planning_artifact(context, "build_question_constraints"))
    proposal_data = _planning_artifact(context, "generate_question_proposals")
    proposal = QuestionProposalGenerationResult(proposals=[QuestionDraftProposal(**row) for row in proposal_data.get("proposals") or []], generation_mode=proposal_data["generation_mode"], generation_warnings=list(proposal_data.get("generation_warnings") or []), llm_trace=proposal_data.get("llm_trace"))
    rule = QuestionRuleDerivationResult(**_planning_artifact(context, "derive_first_interview_rules"))
    presentation = FirstInterviewPlanPresentationService().build(planning_input=_planning_input_from(source), constraints=constraints, proposal_result=proposal, rule=rule)
    return StepOutcome.succeeded(data=asdict(presentation))


def _publish_planning_step(context) -> StepOutcome:
    """事务外读取五个阶段草稿；短事务只发布正式题单版本。"""
    return StepOutcome.succeeded(data={
        "source": _planning_artifact(context, "freeze_first_interview_sources"),
        "constraints": _planning_artifact(context, "build_question_constraints"),
        "proposal": _planning_artifact(context, "generate_question_proposals"),
        "rule": _planning_artifact(context, "derive_first_interview_rules"),
        "presentation": _planning_artifact(context, "build_first_interview_presentation"),
    })


def _publish_planning_persist(db, context, _outcome) -> dict[str, Any]:
    prepared = dict(_outcome.data or {})
    source = dict(prepared.get("source") or {})
    constraints = QuestionPlanningConstraintSet(**dict(prepared.get("constraints") or {}))
    proposal_data = dict(prepared.get("proposal") or {})
    proposal = QuestionProposalGenerationResult(proposals=[QuestionDraftProposal(**row) for row in proposal_data.get("proposals") or []], generation_mode=proposal_data["generation_mode"], generation_warnings=list(proposal_data.get("generation_warnings") or []), llm_trace=proposal_data.get("llm_trace"))
    rule = QuestionRuleDerivationResult(**dict(prepared.get("rule") or {}))
    presentation = FirstInterviewPlanPresentationResult(**dict(prepared.get("presentation") or {}))
    run = db.get(WorkflowRun, context.workflow_run_id)
    app = db.get(Application, str(source["applicationId"])) if run else None
    user = db.get(User, str(source["userId"])) if run else None
    assessment = db.get(ApplicationAssessmentVersion, str(source["sourceAssessmentVersionId"])) if run else None
    if run is None or run.status != "running" or run.lease_owner != context.worker_id or app is None or user is None or assessment is None or assessment.published_at is None:
        raise RuntimeError("first_interview_publish_context_changed")
    plan = FirstInterviewPlanVersionPublisher(db).publish(user=user, app=app, source_assessment=assessment, template=dict(source.get("template") or {}), planning_input=_planning_input_from(source), constraints=constraints, proposal_result=proposal, rule=rule, presentation=presentation)
    return {"planVersionId": plan.plan_version_id, "applicationId": app.application_id}


def build_first_interview_planning_spec(transition_handler, blocked_handler=None) -> WorkflowSpec:
    """声明一面题单规划的恢复步骤。

    1. 锁定最新已发布的初筛 V1 和题单模板；
    2. 生成问题约束；
    3. 生成题目草案；
    4. 校验并推导规则结果；
    5. 生成前端展示内容；
    6. 一次事务发布 FirstInterviewPlanVersion。
    """
    return WorkflowSpec(
        workflow_type="first_interview_planning_workflow",
        handler=None,
        transition_handler=transition_handler,
        blocked_handler=blocked_handler,
        definition_version=1,
        steps=(
            # 步骤 1：锁定初筛评估版本、模板与岗位申请的当前状态。
            StepDefinition("freeze_first_interview_sources", 1, _freeze_planning_step, _step_input, StepPolicy(timeout_seconds=45, max_attempts=2, total_deadline_seconds=180), _planning_persist("first_interview_source_manifest"), artifact_type="first_interview_source_manifest"),
            # 步骤 2：将 V1 结论和模板规则转为可执行的题目约束集。
            StepDefinition("build_question_constraints", 2, _constraints_step, _step_input, StepPolicy(timeout_seconds=90, max_attempts=2, total_deadline_seconds=300), _planning_persist("first_interview_constraints"), artifact_type="first_interview_constraints"),
            # 步骤 3：基于约束集调用题单生成能力，形成尚未发布的题目草案。
            StepDefinition("generate_question_proposals", 3, _proposal_step, _step_input, StepPolicy(timeout_seconds=240, max_attempts=3, total_deadline_seconds=1200, external=True), _planning_persist("first_interview_proposals"), artifact_type="first_interview_proposals"),
            # 步骤 4：按规则检查题目覆盖度、风险和追问约束。
            StepDefinition("derive_first_interview_rules", 4, _rule_step, _step_input, StepPolicy(timeout_seconds=90, max_attempts=2, total_deadline_seconds=300), _planning_persist("first_interview_rule_result"), artifact_type="first_interview_rule_result"),
            # 步骤 5：整理题单确认页面需要的标题、摘要和阅读顺序。
            StepDefinition("build_first_interview_presentation", 5, _presentation_step, _step_input, StepPolicy(timeout_seconds=90, max_attempts=2, total_deadline_seconds=300), _planning_persist("first_interview_presentation"), artifact_type="first_interview_presentation"),
            # 步骤 6：在同一短事务中发布唯一正式题单版本。
            StepDefinition("publish_first_interview_plan", 6, _publish_planning_step, _step_input, StepPolicy(timeout_seconds=90, max_attempts=2, total_deadline_seconds=300), _publish_planning_persist),
        ),
    )
