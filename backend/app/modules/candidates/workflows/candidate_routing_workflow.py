"""候选人岗位分发：冻结输入 → 外部岗位匹配 → 发布 Application 并入队硬筛。"""
from __future__ import annotations

from datetime import UTC, datetime

from backend.app.db.session import SessionLocal
from backend.app.infrastructure.workflow_runtime.artifact_store import WorkflowArtifactStore
from backend.app.models.entities import Candidate, ResumeProfileRecord, ResumeSubmission, SourceDocument, User, WorkflowRun
from backend.app.modules.candidates.intake_process_service import CandidateIntakeProcessService
from backend.app.modules.candidates.intake_service import CandidateIntakeService
from backend.app.modules.candidates.services.candidate_routing_activity_service import CandidateRoutingActivityService
from backend.app.shared.workflows import ActivityOutcomeKind, RecoveryAction, StepDefinition, StepErrorCategory, StepOutcome, StepPolicy, WorkflowSpec
from recruitment_ai_core.execution import model_call_scope

WORKFLOW_TYPE = "candidate_routing_workflow"
INPUT_SCHEMA_VERSION = "candidate_routing_input_v1"


def _input(run_id: str) -> dict:
    with SessionLocal() as db:
        run = db.get(WorkflowRun, run_id)
        if run is None:
            raise RuntimeError("workflow_run_not_found")
        return dict(run.input_json or {})


def _freeze_handler(context) -> StepOutcome:
    """步骤 1：确认 Candidate、简历画像、文件与入队时冻结的来源完全一致。"""
    with SessionLocal() as db:
        run = db.get(WorkflowRun, context.workflow_run_id)
        if run is None or run.subject_type != "resume_submission" or not run.subject_id:
            raise RuntimeError("candidate_routing_subject_invalid")
        submission = db.get(ResumeSubmission, run.subject_id)
        candidate = db.get(Candidate, submission.candidate_id) if submission and submission.candidate_id else None
        document = db.get(SourceDocument, submission.source_document_id) if submission else None
        user = db.get(User, submission.uploaded_by) if submission else None
        profile = db.get(ResumeProfileRecord, candidate.current_resume_profile_id) if candidate and candidate.current_resume_profile_id else None
        if submission is None or candidate is None or document is None or user is None or profile is None:
            raise RuntimeError("candidate_routing_context_missing")
        expected = {
            "schemaVersion": INPUT_SCHEMA_VERSION,
            "resumeSubmissionId": submission.resume_submission_id,
            "candidateId": candidate.candidate_id,
            "resumeProfileId": profile.resume_profile_id,
            "sourceDocumentId": document.source_document_id,
            "sourceSha256": document.source_sha256,
        }
        if any((run.input_json or {}).get(key) != value for key, value in expected.items()):
            raise RuntimeError("candidate_routing_input_mismatch")
        return StepOutcome.succeeded(data=expected)


def _freeze_persist(db, context, outcome) -> dict:
    return WorkflowArtifactStore().persist_outcome_json(
        db, workflow_run_id=context.workflow_run_id,
        artifact_type="candidate_routing_source_manifest", outcome=outcome,
    )


def _read_artifact(context, step_name: str) -> dict:
    refs = context.previous_output_refs.get(step_name) or {}
    artifact_id = str(refs.get("artifactId") or "")
    if not artifact_id:
        raise RuntimeError(f"candidate_routing_previous_artifact_missing:{step_name}")
    with SessionLocal() as db:
        return WorkflowArtifactStore().get_json(db, artifact_id)


def _match_handler(context) -> StepOutcome:
    """步骤 2：事务外执行专业—岗位匹配，只生成可审计的匹配草稿。"""
    manifest = _read_artifact(context, "freeze_routing_sources")
    with SessionLocal() as db:
        submission = db.get(ResumeSubmission, str(manifest["resumeSubmissionId"]))
        candidate = db.get(Candidate, str(manifest["candidateId"]))
        if submission is None or candidate is None:
            raise RuntimeError("candidate_routing_match_context_missing")
        routing_input = CandidateIntakeService(db).prepare_routing_input(
            submission=submission,
            candidate=candidate,
        )
        # 岗位分发只读取已发布教育数组中的专业及其来源，不再从基础信息扁平字段
        # 或 Submission 元数据拼出第二套专业事实。
        source_quote = str(routing_input.get("majorSourceQuote") or "")
    # 算法包通过模型网关调用 LLM。该请求是 Step 内唯一的 ExternalActivity，
    # 成功后 ActivityRunner 立即保存草稿；恢复时不会再重复请求模型。
    with model_call_scope(context.external_request_id, "candidate_major_routing"):
        batch = CandidateRoutingActivityService().decide(
            context,
            routing_input=routing_input,
            source_quote=source_quote,
        )
    if batch.status in {ActivityOutcomeKind.COMPLETED.value, ActivityOutcomeKind.DEGRADED.value}:
        return StepOutcome.succeeded(
            data=dict(batch.results["candidate_major_routing"].get("decision") or {})
        )
    if batch.status == ActivityOutcomeKind.BLOCKED.value:
        return StepOutcome.blocked(
            error_code=batch.error_code or "candidate_routing_review_required",
            error_message=batch.error_message or "岗位匹配需要人工选择",
            recovery_action=RecoveryAction.REVIEW_REQUIRED,
        )
    if batch.status == "retry_wait":
        return StepOutcome.activity_retry_wait(
            error_code=batch.error_code or "candidate_major_routing_retry_wait",
            error_message=batch.error_message or "候选人岗位匹配活动等待重试",
            retry_after_seconds=batch.retry_after_seconds or 15,
        )
    return StepOutcome.failed(
        error_code=batch.error_code or "candidate_major_routing_activity_failed",
        error_message=batch.error_message or "候选人岗位匹配活动失败",
        error_category=batch.error_category or StepErrorCategory.EXTERNAL_PERMANENT,
    )


def _match_persist(db, context, outcome) -> dict:
    return WorkflowArtifactStore().persist_outcome_json(
        db, workflow_run_id=context.workflow_run_id,
        artifact_type="candidate_routing_decision", outcome=outcome,
    )


def _publish_handler(context) -> StepOutcome:
    """步骤 3：事务外读取匹配草稿；短事务只发布 Application 与队列消息。"""
    return StepOutcome.succeeded(data={
        "manifest": _read_artifact(context, "freeze_routing_sources"),
        "decision": _read_artifact(context, "match_candidate_jobs"),
    })


def _publish_persist(db, context, _outcome) -> dict:
    payload = dict(_outcome.data or {})
    manifest = dict(payload.get("manifest") or {})
    decision = dict(payload.get("decision") or {})
    submission = db.get(ResumeSubmission, str(manifest["resumeSubmissionId"]))
    candidate = db.get(Candidate, str(manifest["candidateId"]))
    profile = db.get(ResumeProfileRecord, str(manifest["resumeProfileId"]))
    document = db.get(SourceDocument, str(manifest["sourceDocumentId"]))
    run = db.get(WorkflowRun, context.workflow_run_id)
    user = db.get(User, run.triggered_by) if run else None
    if (
        run is None
        or run.status != "running"
        or run.lease_owner != context.worker_id
        or submission is None
        or candidate is None
        or profile is None
        or document is None
        or user is None
    ):
        raise RuntimeError("candidate_routing_publish_context_missing")
    CandidateIntakeProcessService.activate_candidate(candidate, now=datetime.now(UTC).replace(tzinfo=None))
    application_ids = CandidateIntakeService(db).publish_routing(
        user=user,
        submission=submission,
        candidate=candidate,
        profile=profile,
        document=document,
        decision=decision,
        routing_workflow_run_id=context.workflow_run_id,
    )
    return {"candidateId": candidate.candidate_id, "applicationIds": application_ids}


def build_candidate_routing_spec(transition_handler, blocked_handler=None) -> WorkflowSpec:
    """声明候选人岗位分发的可恢复步骤。

    1. 冻结 Candidate、ResumeProfile、岗位版本和源文件；
    2. 调用岗位匹配模型，保存决策草稿；
    3. 在一个短事务中创建 Application，并把硬筛任务加入队列。
    """
    return WorkflowSpec(
        workflow_type=WORKFLOW_TYPE,
        handler=None,
        transition_handler=transition_handler,
        blocked_handler=blocked_handler,
        definition_version=1,
        steps=(
            # 步骤 1：确认候选人与简历画像仍是启动分发时的同一版本。
            StepDefinition("freeze_routing_sources", 1, _freeze_handler, _input, StepPolicy(timeout_seconds=30, max_attempts=1, total_deadline_seconds=120), _freeze_persist, artifact_type="candidate_routing_source_manifest"),
            # 步骤 2：模型完成专业—岗位匹配；失败只重试本次外部活动，不创建申请。
            StepDefinition("match_candidate_jobs", 2, _match_handler, _input, StepPolicy(timeout_seconds=180, max_attempts=3, total_deadline_seconds=900, external=True), _match_persist, artifact_type="candidate_routing_decision"),
            # 步骤 3：读取已持久化匹配草稿，原子发布申请与后续硬筛队列消息。
            StepDefinition("publish_applications_and_enqueue_hard_screening", 3, _publish_handler, _input, StepPolicy(timeout_seconds=60, max_attempts=2, total_deadline_seconds=300), _publish_persist),
        ),
    )
