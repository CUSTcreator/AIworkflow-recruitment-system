"""面评 Workflow 使用的底层运行处理器。

正式的七步顺序和 Artifact 合同定义在 ``post_interview_scoring_workflow``。
本模块只封装可恢复运行所需的冻结来源、面评解析、状态迁移和原子发布处理器；
拓扑增量评分由算法包的统一入口负责。这里的函数是实现组件，不是第二套 Workflow 定义。
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from backend.app.db.session import SessionLocal
from backend.app.infrastructure.workflow_runtime.artifact_store import WorkflowArtifactStore
from backend.app.models.entities import Application, Interview, User, WorkflowRun
from backend.app.modules.assessment.domain.assessment_version import (
    AssessmentSourceManifest,
    AssessmentStage,
)
from backend.app.modules.assessment.services.post_interview_assessment_publisher import (
    PostInterviewAssessmentPublisher,
)
from backend.app.modules.assessment.services.source_service import (
    AssessmentSource,
    AssessmentSourceService,
)
from backend.app.modules.interviews.services.post_interview_activity_service import (
    PostInterviewActivityService,
)
from backend.app.shared.workflows import RecoveryAction, StepErrorCategory, StepOutcome, StepPolicy
from recruitment_ai_core.incremental_scoring import IncrementalScoringResult
from recruitment_ai_core.interview_evaluation import (
    InterviewParseDraft,
    InterviewParseInput,
    extract_and_bind_interview_evidence,
    has_interview_evidence,
)


ArtifactPersistor = Callable[[Any, Any, StepOutcome], dict[str, Any]]


def _json_artifact_persistor(artifact_type: str) -> ArtifactPersistor:
    """为中间步骤创建统一的 JSON Artifact 持久化函数。"""

    def persist(db: Any, context: Any, outcome: StepOutcome) -> dict[str, Any]:
        return WorkflowArtifactStore().persist_outcome_json(
            db,
            workflow_run_id=context.workflow_run_id,
            artifact_type=artifact_type,
            outcome=outcome,
        )

    return persist


def _load_artifact(context: Any, step_name: str) -> dict[str, Any]:
    """读取前序步骤成功后保存的 Artifact；缺失即表示恢复边界被破坏。"""

    output_refs = context.previous_output_refs.get(step_name) or {}
    artifact_id = str(output_refs.get("artifactId") or "")
    with SessionLocal() as db:
        value = WorkflowArtifactStore().get_json(db, artifact_id)
    if not isinstance(value, Mapping):
        raise RuntimeError(f"post_interview_step_artifact_missing:{step_name}")
    return dict(value)


def _source_to_artifact(source: AssessmentSource) -> dict[str, Any]:
    """把冻结来源转换为可恢复的 JSON，不把 ORM 对象带出 Step。"""

    return {
        "applicationId": source.application_id,
        "candidateId": source.candidate_id,
        "stage": source.stage.value,
        "previousAssessment": dict(source.previous_assessment),
        "previousCoreResult": dict(source.previous_core_result),
        "previousRuleResult": dict(source.previous_rule_result),
        # V2/V3 的可评分范围必须来自上一正式版的冻结拓扑，而非运行时重建。
        "previousTopology": dict(source.previous_topology),
        "resumeProfile": dict(source.resume_profile),
        "jobRequirementProfile": dict(source.job_requirement_profile),
        "interviewRecords": [dict(item) for item in source.interview_records],
        "openTargets": [dict(item) for item in source.open_targets],
        "priorInterviewParseResults": [dict(item) for item in source.prior_interview_parse_results],
        "sourceManifest": source.source_manifest.model_dump(mode="json"),
        "firstInterviewPlan": (
            dict(source.first_interview_plan) if source.first_interview_plan else None
        ),
    }


def _source_from_artifact(value: Mapping[str, Any]) -> AssessmentSource:
    """从冻结 Artifact 恢复领域无关的评分来源快照。"""

    return AssessmentSource(
        application_id=str(value["applicationId"]),
        candidate_id=str(value["candidateId"]),
        stage=AssessmentStage(str(value["stage"])),
        previous_assessment=dict(value["previousAssessment"]),
        previous_core_result=dict(value["previousCoreResult"]),
        previous_rule_result=dict(value["previousRuleResult"]),
        previous_topology=dict(value["previousTopology"]),
        resume_profile=dict(value["resumeProfile"]),
        job_requirement_profile=dict(value["jobRequirementProfile"]),
        interview_records=tuple(value.get("interviewRecords") or ()),
        open_targets=tuple(value.get("openTargets") or ()),
        prior_interview_parse_results=tuple(value.get("priorInterviewParseResults") or ()),
        source_manifest=AssessmentSourceManifest.model_validate(value["sourceManifest"]),
        first_interview_plan=value.get("firstInterviewPlan"),
    )


def _frozen_source(context: Any) -> AssessmentSource:
    """读取本次 V2/V3 运行唯一允许使用的冻结来源。"""

    artifact = _load_artifact(context, "freeze_post_interview_sources")
    raw_source = artifact.get("source")
    if not isinstance(raw_source, Mapping):
        raise RuntimeError("post_interview_source_manifest_missing")
    return _source_from_artifact(raw_source)


def _active_items(value: Any) -> tuple[dict[str, Any], ...]:
    """保留冻结画像中的有效事实；面评不得引用已删除或历史失效项。"""

    if not isinstance(value, list):
        return ()
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        row = dict(item)
        if row.get("is_current") is False or row.get("active") is False:
            continue
        if str(row.get("status") or "").lower() in {"inactive", "deleted", "superseded"}:
            continue
        result.append(row)
    return tuple(result)


def _effective_work_units(resume_profile: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """Read all effective WorkUnits from published nested experience units."""
    from recruitment_ai_core.screening_scoring.profile_evidence import nested_work_units

    return _active_items(nested_work_units(dict(resume_profile)))


def _effective_skill_claims(resume_profile: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """提取本版 ResumeProfile 内全部有效 SkillClaim，供事实更正精确匹配。"""

    values = list(_active_items(resume_profile.get("skill_claims")))
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for item in values:
        item_id = str(item.get("skill_claim_id") or item.get("skillClaimId") or "")
        if not item_id or item_id in seen:
            continue
        seen.add(item_id)
        result.append(item)
    return tuple(result)


def _job_capabilities(job_profile: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """返回已冻结 JobRequirementProfile 的完整有效 JDCapability 集合。"""

    direct = _active_items(job_profile.get("job_capabilities"))
    if direct:
        return direct
    values: list[dict[str, Any]] = []
    for unit in _active_items(job_profile.get("jd_units")):
        unit_id = unit.get("jd_unit_id") or unit.get("jdUnitId")
        for capability in _active_items(unit.get("job_capabilities") or unit.get("capabilities")):
            values.append({**capability, "jd_unit_id": capability.get("jd_unit_id") or unit_id})
    return tuple(values)


def _preset_indicator_definitions(job_profile: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """读取岗位固化的预设经历模型指标（当前标准配置为九项）。"""

    direct = _active_items(job_profile.get("preset_indicator_definitions"))
    if direct:
        return direct
    for key in ("preset_model", "preset_model_config", "preset_experience_model"):
        model = _as_mapping(job_profile.get(key))
        indicators = _active_items(model.get("indicators"))
        if indicators:
            return indicators
    return ()


def _confirmed_questions(first_interview_plan: Mapping[str, Any] | None) -> tuple[dict[str, Any], ...]:
    """只使用冻结时已确认且精确绑定计划版本的正式 Question。"""

    if not isinstance(first_interview_plan, Mapping):
        return ()
    return _active_items(first_interview_plan.get("confirmed_questions"))


def _interview_parse_input(source: AssessmentSource) -> InterviewParseInput:
    """组装面评算法的完整冻结输入；不传 Application/Candidate 等业务身份。"""

    # 逐题记录只在进入语义提取前拼接一次题目和回答；自由记录保持原文。
    # ``analysis_text`` 是本轮临时输入，不回写不可变 InterviewRecord。
    confirmed = {
        str(item.get("question_id") or item.get("questionId") or ""): dict(item)
        for item in _confirmed_questions(source.first_interview_plan)
        if isinstance(item, Mapping)
    }
    prepared_records: list[dict[str, Any]] = []
    for raw in source.interview_records:
        row = dict(raw)
        if str(row.get("record_type") or row.get("recordType") or "") == "question_answer":
            source_input = _as_mapping(row.get("source_input") or row.get("sourceInput"))
            question_id = str(row.get("question_id") or row.get("questionId") or "")
            question = confirmed.get(question_id, {})
            question_text = str(
                row.get("question_text")
                or row.get("questionText")
                or source_input.get("questionText")
                or source_input.get("question")
                or question.get("question_text")
                or question.get("questionText")
                or question.get("mainQuestion")
                or question.get("finalText")
                or ""
            ).strip()
            answer_text = str(
                row.get("raw_text")
                or source_input.get("rawText")
                or source_input.get("answerText")
                or source_input.get("answerSummary")
                or ""
            ).strip()
            if answer_text:
                row["analysis_text"] = (
                    f"题目：{question_text}\n回答：{answer_text}"
                    if question_text else answer_text
                )
        prepared_records.append(row)

    return InterviewParseInput(
        stage=source.stage.value,
        interview_records=tuple(prepared_records),
        resume_profile=dict(source.resume_profile),
        job_requirement_profile=dict(source.job_requirement_profile),
        open_targets=tuple(dict(item) for item in source.open_targets),
        confirmed_questions=_confirmed_questions(source.first_interview_plan),
        work_units=_effective_work_units(source.resume_profile),
        skill_claims=_effective_skill_claims(source.resume_profile),
        job_capabilities=_job_capabilities(source.job_requirement_profile),
        preset_indicator_definitions=_preset_indicator_definitions(source.job_requirement_profile),
    )


def _as_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _activity_failure_outcome(batch: Any, *, default_code: str, default_message: str) -> StepOutcome:
    """统一把 ActivityRunner 的等待/终态投影回父 Step，不重跑已成功子活动。"""
    if batch.status == "retry_wait":
        return StepOutcome.activity_retry_wait(
            error_code=batch.error_code or default_code,
            error_message=batch.error_message or default_message,
            retry_after_seconds=max(1, int(batch.retry_after_seconds or 15)),
        )
    return StepOutcome.failed(
        error_code=batch.error_code or default_code,
        error_message=batch.error_message or default_message,
        error_category=batch.error_category or StepErrorCategory.EXTERNAL_PERMANENT,
    )


def build_post_interview_spec(
    *,
    workflow_type: str,
    stage: AssessmentStage,
    previous_stage: AssessmentStage,
    kind: str,
    actions: frozenset[str],
    transition_handler: Callable[..., Any],
) -> Any:
    """返回 V2/V3 七步 Workflow 所需的运行处理器。

    本模块提供步骤 1（冻结来源）、步骤 2（面评语义提取）和步骤 7
    （原子发布）的底层处理器；步骤 3 至 6 由统一七步编排模块组合。

    V2 和 V3 只通过 ``stage``、``previous_stage``、``kind`` 与 ``actions`` 区分，
    因而拥有完全相同的恢复边界，同时不会混用来源版本。
    """

    def step_input(workflow_run_id: str) -> dict[str, Any]:
        with SessionLocal() as db:
            workflow_run = db.get(WorkflowRun, workflow_run_id)
            if workflow_run is None:
                raise RuntimeError("workflow_run_not_found")
            return {
                "applicationId": workflow_run.application_id,
                "action": (workflow_run.input_json or {}).get("action"),
                "stage": stage.value,
                "definitionVersion": 2,
            }

    def freeze_sources(context: Any) -> StepOutcome:
        """步骤 1【冻结来源】：锁定本轮唯一可用的输入版本。

        读取：前一已发布 AAV（V2 必为 V1，V3 必为 V2）及其 ResumeProfile/JobRequirementProfile、
        本轮不可变 InterviewRecord、全部 Open Target；V2 还读取精确绑定计划版本的已确认题单/题目。
        输出：只含对象 ID、版本与 JSON 快照的 ``AssessmentSource`` Artifact；禁止创建 IPR/AAV、
        禁止使用查询时的“最新”对象。成功持久化时才把 Interview 标为 ``scoring``。
        """

        with SessionLocal() as db:
            workflow_run = db.get(WorkflowRun, context.workflow_run_id)
            application = (
                db.get(Application, workflow_run.application_id) if workflow_run else None
            )
            user = db.get(User, workflow_run.triggered_by) if workflow_run else None
            payload = workflow_run.input_json or {} if workflow_run else {}
            action = str(payload.get("action") or "")
            body = payload.get("body") if isinstance(payload.get("body"), Mapping) else {}
            if (
                workflow_run is None
                or workflow_run.status != "running"
                or workflow_run.lease_owner != context.worker_id
                or application is None
                or user is None
                or action not in actions
            ):
                raise RuntimeError("post_interview_context_invalid")
            interview = db.get(Interview, f"INT_{application.application_id}_{kind.upper()}")
            if interview is None:
                raise RuntimeError("post_interview_interview_missing")
            try:
                source = AssessmentSourceService(db).lock(
                    user=user,
                    application=application,
                    body=dict(body),
                    stage=stage,
                    previous_stage=previous_stage,
                    interview_kind=kind,
                )
            except RuntimeError as error:
                code = str(error)
                # 冻结输入缺失或版本不一致不能靠重复调用外部服务解决，直接交给
                # 用户检查上一正式版本/面评来源；未知运行时异常仍进入有限重试。
                if code.startswith("assessment_"):
                    return StepOutcome.blocked(
                        error_code=code.split(":", 1)[0],
                        error_message="上一版评估或本轮面评来源不可用，请检查来源后重新计算",
                        error_category=StepErrorCategory.VALIDATION,
                        recovery_action=RecoveryAction.REVIEW_REQUIRED,
                    )
                raise
            return StepOutcome.succeeded(
                data={
                    "source": _source_to_artifact(source),
                    "applicationId": application.application_id,
                    "userId": user.user_id,
                    "interviewId": interview.interview_id,
                    "action": action,
                    "body": dict(body),
                }
            )

    def persist_frozen_sources(db: Any, context: Any, outcome: StepOutcome) -> dict[str, Any]:
        """保存来源清单，并在同一短事务中把当前面试切换为 scoring。"""

        refs = _json_artifact_persistor(f"{stage.value}_source_manifest")(db, context, outcome)
        interview = db.get(Interview, outcome.data["interviewId"])
        if interview is None:
            raise RuntimeError("post_interview_interview_missing_after_freeze")
        interview.status = "scoring"
        return refs

    def extract_interview_units(context: Any) -> StepOutcome:
        """执行唯一的面评语义提取 Activity。"""
        parse_input = _interview_parse_input(_frozen_source(context))
        if not has_interview_evidence(parse_input.interview_records):
            result = extract_and_bind_interview_evidence(parse_input)
            return StepOutcome.succeeded(data={
                "parseInput": parse_input.as_dict(), "evidenceExtraction": result.as_dict(),
            })
        batch = PostInterviewActivityService().extract_and_bind_evidence(context, parse_input)
        if batch.is_usable:
            data = dict(batch.results["interview_evidence_extraction"])
            if batch.status == "degraded":
                data["degraded"] = True
                data["activityQuality"] = dict(batch.quality_summaries)
            return StepOutcome.succeeded(data=data)
        return _activity_failure_outcome(
            batch,
            default_code="interview_evidence_extraction_activity_failed",
            default_message="面评证据提取活动失败",
        )

    def publish(context: Any) -> StepOutcome:
        """第 7 步【发布准备】：实际领域写入只能在 ``persist_success`` 的单一短事务完成。

        本 handler 不做数据库操作；这样 StepRunner 能把“正式产物落库”和 succeeded 检查点放进
        同一事务，避免只发布 IPR 或只发布 AAV 的半成品。
        """

        return StepOutcome.succeeded(data={
            "frozen": _load_artifact(context, "freeze_post_interview_sources"),
            "parseDraft": _load_artifact(context, "parse_interview_units"),
            "core": _load_artifact(context, "run_topology_incremental_scoring"),
            "rule": _load_artifact(context, "derive_incremental_rules"),
            "presentation": _load_artifact(context, "generate_incremental_presentation"),
        })

    def persist_published_assessment(
        db: Any,
        context: Any,
        outcome: StepOutcome,
    ) -> dict[str, Any]:
        """步骤 7 的唯一事务边界。

        读取：冻结来源、最终面评草稿、拓扑评分、规则结果、展示结果，以及当前
        WorkflowRun/Application/User/Interview。
        产生：一条新的 IPR 和一条新的 AAV；发布器同时更新本申请的 Target 状态、
        Interview/Application 状态、Task 和 StageHistory。既有 IPR/AAV/画像版本绝不覆盖。
        """

        prepared = _as_mapping(outcome.data)
        frozen = _as_mapping(prepared.get("frozen"))
        source = _source_from_artifact(_as_mapping(frozen.get("source")))
        workflow_run = db.get(WorkflowRun, context.workflow_run_id)
        application = db.get(Application, source.application_id) if workflow_run else None
        user = db.get(User, frozen.get("userId")) if workflow_run else None
        interview = db.get(Interview, frozen.get("interviewId"))
        if (
            workflow_run is None
            or workflow_run.status != "running"
            or workflow_run.lease_owner != context.worker_id
            or application is None
            or user is None
            or interview is None
        ):
            raise RuntimeError("post_interview_publish_context_changed")
        parse_payload = _as_mapping(prepared.get("parseDraft"))
        draft = InterviewParseDraft.from_dict(_as_mapping(parse_payload.get("parseDraft") or parse_payload))
        core_artifact = _as_mapping(prepared.get("core"))
        core_payload = _as_mapping(core_artifact.get("coreResult"))
        core_result = IncrementalScoringResult.from_dict(core_payload)
        rule_result = _as_mapping(
            _as_mapping(prepared.get("rule")).get("ruleResult")
        )
        # 步骤 6 的 Artifact 直接保存 AssessmentPresentationResult 合同，
        # 不再额外套一层 presentationResult。发布阶段必须读取同一结构，
        # 否则会把合法产物误读为 {}，并在 Pydantic 校验时失败。
        presentation_result = _as_mapping(prepared.get("presentation"))
        published = PostInterviewAssessmentPublisher(db).publish(
            user=user,
            application=application,
            interview=interview,
            source=source,
            interview_parse_draft=draft,
            core_result=core_result,
            rule_result=rule_result,
            presentation_result=presentation_result,
            previous_topology=source.previous_topology,
            # 旧核心结果还未产生节点状态时，保留冻结状态，保证发布不会生成空 Snapshot。
            topology_core_result={
                **core_result.as_dict(),
                "topologyStates": _as_mapping(
                    _as_mapping(core_artifact.get("topologyResult")).get("coreResult")
                ).get("topologyStates") or {
                    str(item.get("nodeId") or ""): {"score": item.get("score"), "level": item.get("level"), "state": item.get("state")}
                    for item in source.previous_topology.get("nodes") or ()
                    if str(item.get("nodeId") or "")
                },
            },
            # 发布幂等必须读取当前 Workflow 的业务请求身份。冻结 Artifact 只保存
            # 评分来源，不能承载用户重算标识，否则检查点复用会把新请求误认成旧请求。
            workflow_body=dict(workflow_run.input_json or {}),
        )
        return dict(published)

    # Runtime handler registry; public V2/V3 workflow owns the seven-step definition.
    from types import SimpleNamespace
    return SimpleNamespace(steps=(
        SimpleNamespace(name="freeze_post_interview_sources", handler=freeze_sources, input_factory=step_input, policy=StepPolicy(timeout_seconds=60, max_attempts=2, total_deadline_seconds=240), persist_success=persist_frozen_sources),
        SimpleNamespace(name="extract_interview_units", handler=extract_interview_units, input_factory=step_input, policy=StepPolicy(timeout_seconds=240, max_attempts=2, total_deadline_seconds=900, external=True), persist_success=_json_artifact_persistor(f"{stage.value}_interview_extraction")),
        SimpleNamespace(name="publish_post_interview_assessment", handler=publish, input_factory=step_input, policy=StepPolicy(timeout_seconds=90, max_attempts=2, total_deadline_seconds=300), persist_success=persist_published_assessment),
    ))







