"""V2/V3 面评后的统一七步 Workflow 编排。

本模块是系统层正式入口，定义步骤顺序、Artifact 边界和重试策略：
冻结来源 → 面评单元提取 → 锚点评估 → 拓扑增量评分 Pipeline → 规则派生 → 展示加工 → 原子发布。
拓扑评分公式由算法包实现；``post_interview_runtime`` 仅提供底层处理器。
"""

from __future__ import annotations

from typing import Any

from backend.app.db.session import SessionLocal
from backend.app.infrastructure.workflow_runtime.artifact_store import (
    WorkflowArtifactStore,
)
from backend.app.modules.assessment.domain.assessment_version import AssessmentStage
from backend.app.modules.interviews.services.post_interview_activity_service import (
    PostInterviewActivityService,
)
from backend.app.modules.interviews.workflows.post_interview_runtime import (
    _frozen_source,
    _interview_parse_input,
    build_post_interview_spec,
)
from backend.app.modules.interviews.workflows.post_interview_topology_plan import (
    POST_INTERVIEW_TOPOLOGY_DEFINITION_VERSION,
)
from backend.app.shared.workflows import (
    RecoveryAction,
    StepDefinition,
    StepErrorCategory,
    StepOutcome,
    StepPolicy,
    WorkflowSpec,
)
from recruitment_ai_core.incremental_scoring import (
    AnchorJudgement,
    IncrementalEvidence,
    InterviewAssertion,
    run_topology_incremental_scoring,
    topology_result_as_dict,
)
from recruitment_ai_core.interview_evaluation import (
    InterviewEvidenceExtractionResult,
    InterviewParseInput,
    normalize_interview_evidence,
)

STEP_NAMES = (
    "freeze_post_interview_sources",
    "parse_interview_units",
    "evaluate_anchor_judgements",
    "run_topology_incremental_scoring",
    "derive_incremental_rules",
    "generate_incremental_presentation",
    "publish_post_interview_assessment",
)


def _persist_json(artifact_type: str):
    """将当前步骤的 JSON 结果写入该步骤唯一的 Artifact。"""

    def persist(db: Any, context: Any, outcome: StepOutcome) -> dict[str, Any]:
        return WorkflowArtifactStore().persist_outcome_json(
            db,
            workflow_run_id=context.workflow_run_id,
            artifact_type=artifact_type,
            outcome=outcome,
        )

    return persist


def _assertions(draft: dict[str, Any]) -> list[dict[str, Any]]:
    """返回解析阶段生成的唯一 assertion 集合。"""
    return [
        dict(item) for item in draft.get("assertions") or () if isinstance(item, dict)
    ]


def _build_scoring_assertions(
    parsed: dict[str, Any],
    judged: dict[str, Any],
) -> tuple[InterviewAssertion, ...]:
    """Bind validated anchor judgements back to their parsed interview units."""

    by_id = {
        str(item["assertionId"]): item
        for item in parsed.get("assertions") or ()
        if isinstance(item, dict)
        and item.get("assertionId")
        and item.get("isCapabilityEvaluation")
    }
    decisions: dict[str, dict[str, AnchorJudgement]] = {}
    for row in judged.get("judgements") or ():
        if (
            isinstance(row, dict)
            and row.get("unitId")
            and row.get("anchorId")
            and str(row.get("judgement") or "") != "not_support"
        ):
            decisions.setdefault(str(row["unitId"]), {})[str(row["anchorId"])] = (
                AnchorJudgement(str(row["judgement"]))
            )

    assertions: list[InterviewAssertion] = []
    for assertion_id, candidate_judgements in decisions.items():
        parsed_assertion = by_id.get(assertion_id)
        if parsed_assertion is None:
            continue
        assertions.append(
            InterviewAssertion(
                assertion_id=assertion_id,
                text=str(parsed_assertion["text"]),
                source_quote=str(parsed_assertion["sourceQuote"]),
                source_refs=tuple(parsed_assertion["sourceRefs"]),
                judgement=next(iter(candidate_judgements.values())),
                candidate_anchor_ids=tuple(candidate_judgements),
                candidate_judgements=candidate_judgements,
            )
        )
    return tuple(assertions)


def _anchor_evaluation_plans(
    assertions: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
) -> tuple[tuple[str, list[dict[str, Any]], list[dict[str, Any]]], ...]:
    """让每个冻结锚点只进入一侧，同时保留经历侧的专门约束。"""
    experience_units = [item for item in assertions if item.get("isExperienceRelated")]
    eligible = [node for node in nodes if node.get("interviewEligible") is not False]
    experience_anchors = [node for node in eligible if _anchor_region(node) == "experience"]
    # ``both`` 表示该锚点可被任意能力面评更新；放到覆盖全部 assertion 的岗位侧
    # 即可，不能再在经历侧重复请求并产生互相覆盖的两份判断。
    job_anchors = [node for node in eligible if _anchor_region(node) != "experience"]
    return (
        ("experience", experience_units, experience_anchors),
        ("job", assertions, job_anchors),
    )


def _anchor_region(node: dict[str, Any]) -> str:
    """优先消费冻结来源装配的区域；缺失时按稳定引用做保守推断。"""
    explicit = str(node.get("region") or "")
    if explicit in {"experience", "job", "both"}:
        return explicit
    kinds = {
        str(item.get("kind") or item.get("reference_kind") or "")
        for item in node.get("references") or ()
        if isinstance(item, dict)
    }
    if kinds & {"work_unit", "project", "indicator", "framework", "pao"}:
        return "experience"
    if kinds & {"job_unit", "job_capability", "skill_claim", "jd_unit"}:
        return "job"
    return "both"


def _source_contract_failure(error: Exception) -> StepOutcome | None:
    """Preserve known source-contract codes before the generic StepRunner boundary.

    The generic runner correctly keeps unexpected programming errors as internal
    failures, but it intentionally stores their exception class as the error code.
    Topology contract errors are different: they identify an unusable upstream AAV
    source and must route users to source repair rather than a blind scoring retry.
    Only explicit, documented codes are translated here; arbitrary exception text is
    never promoted to a business recovery code.
    """

    code = str(error).split(":", 1)[0].strip()
    if not code.startswith(("topology_", "post_interview_topology_")):
        return None
    return StepOutcome.failed(
        error_code=code,
        error_message="上一版评估的评分拓扑不可用，请先修复来源后重新计算。",
        error_category=StepErrorCategory.VALIDATION,
        recovery_action=RecoveryAction.REVIEW_REQUIRED,
    )


def build_post_interview_scoring_spec(
    *,
    workflow_type: str,
    stage: AssessmentStage,
    previous_stage: AssessmentStage,
    kind: str,
    actions: frozenset[str],
    transition_handler: Any,
    blocked_handler: Any = None,
) -> WorkflowSpec:
    """为一面后 V2 或二面后 V3 构造相同拓扑的七步定义。"""
    runtime = build_post_interview_spec(
        workflow_type=workflow_type,
        stage=stage,
        previous_stage=previous_stage,
        kind=kind,
        actions=actions,
        transition_handler=transition_handler,
    )
    handlers = {step.name: step for step in runtime.steps}
    factory = handlers["freeze_post_interview_sources"].input_factory

    def parse_units(context: Any) -> StepOutcome:
        parse_input = _interview_parse_input(_frozen_source(context))
        extracted = handlers["extract_interview_units"].handler(context)
        if extracted.kind.value != "succeeded":
            return extracted
        raw = dict(extracted.data or {})
        parse_input = InterviewParseInput.from_dict(
            dict(raw.get("parseInput") or parse_input.as_dict())
        )
        extraction = InterviewEvidenceExtractionResult.from_dict(
            dict(raw.get("evidenceExtraction") or {})
        )
        draft = normalize_interview_evidence(parse_input, extraction)
        if draft.review_required:
            return StepOutcome.blocked(
                error_code="post_interview_parse_review_required",
                error_message="面评记录存在无法可靠绑定的内容，请修改本轮面评后重新计算",
                error_category=StepErrorCategory.VALIDATION,
                recovery_action=RecoveryAction.REVIEW_REQUIRED,
            )
        data = {
            "parseDraft": draft.as_dict(),
            "assertions": [dict(item) for item in draft.assertions],
        }
        if raw.get("degraded"):
            data["degraded"] = True
            data["activityQuality"] = dict(raw.get("activityQuality") or {})
        return StepOutcome.succeeded(data=data)

    def evaluate(context: Any) -> StepOutcome:
        # 侧路由只在本 Workflow 层决定：岗位侧对所有能力断言必做；经历相关
        # 断言先做经历侧，全部完成后再做岗位侧。算法包中的 topology route
        # 仅负责把判断映射到冻结拓扑区域，不负责在两侧之间二选一。
        source = _frozen_source(context)
        artifact_id = context.previous_output_refs["parse_interview_units"][
            "artifactId"
        ]
        with SessionLocal() as db:
            parsed = WorkflowArtifactStore().get_json(db, artifact_id)
        assertions = [
            {**dict(item), "unitId": str(item.get("assertionId") or "")}
            for item in parsed.get("assertions") or ()
            if isinstance(item, dict)
            and item.get("isCapabilityEvaluation")
            and item.get("assertionId")
        ]
        nodes = [
            dict(item)
            for item in source.previous_topology.get("nodes") or ()
            if isinstance(item, dict)
        ]
        if not nodes:
            return StepOutcome.failed(
                error_code="post_interview_topology_anchors_missing",
                error_message="上一版评估没有可用的冻结评分锚点，请先重建评估来源。",
                error_category=StepErrorCategory.VALIDATION,
                recovery_action=RecoveryAction.REVIEW_REQUIRED,
            )
        if not assertions:
            evidence = IncrementalEvidence(
                affected_result_refs=(),
                unchanged_result_refs=(),
                evidence_index={},
                scoring_evidence={"anchor_judgements": []},
                anchor_judgements=(),
                current_interview_record_ids=tuple(
                    source.source_manifest.interview_record_ids
                ),
            )
            return StepOutcome.succeeded(
                data={
                    "judgements": [],
                    "evidence": evidence.as_dict(),
                    "noScorableEvidence": True,
                    "allNotSupport": False,
                    "coverage": {
                        "complete": True,
                        "expectedPairCount": 0,
                        "validPairCount": 0,
                    },
                }
            )
        rows: list[dict[str, Any]] = []
        expected_pair_count = 0
        valid_pair_count = 0
        missing_pairs: list[dict[str, str]] = []
        service = PostInterviewActivityService()
        for side, units, anchors in _anchor_evaluation_plans(assertions, nodes):
            if not units or not anchors:
                continue
            semantic_anchors = [
                node for node in anchors if str(node.get("description") or "").strip()
            ]
            if not semantic_anchors:
                return StepOutcome.failed(
                    error_code="post_interview_topology_anchor_semantics_missing",
                    error_message="上一版评分锚点缺少可理解的业务含义，请先重建评估来源。",
                    error_category=StepErrorCategory.VALIDATION,
                    recovery_action=RecoveryAction.REVIEW_REQUIRED,
                )
            try:
                batch = service.evaluate_anchor_side(
                    context,
                    side=side,
                    units=units,
                    anchors=semantic_anchors,
                    workflow_type=workflow_type,
                )
            except ValueError as error:
                if str(error) == "llm_budget_single_item_exceeds_budget":
                    return StepOutcome.failed(
                        error_code="post_interview_anchor_input_too_large",
                        error_message="面评内容或评分锚点超出单次处理上限，请精简面评内容后重新计算。",
                        error_category=StepErrorCategory.VALIDATION,
                        recovery_action=RecoveryAction.REVIEW_REQUIRED,
                    )
                raise
            if not batch.is_usable:
                if batch.status == "retry_wait":
                    return StepOutcome.activity_retry_wait(
                        error_code=batch.error_code
                        or "post_interview_anchor_model_unavailable",
                        error_message=batch.error_message
                        or "锚点评估正在等待自动重试。",
                        retry_after_seconds=max(
                            1, int(batch.retry_after_seconds or 15)
                        ),
                    )
                message = str(batch.error_message or "")
                error_code = (
                    "post_interview_anchor_coverage_incomplete"
                    if batch.error_code == "LLMResponseError"
                    and "model_unavailable" not in message
                    else "post_interview_anchor_model_unavailable"
                )
                return StepOutcome.failed(
                    error_code=error_code,
                    error_message=(
                        "模型没有返回完整、可校验的锚点判断，请重试本轮评分。"
                        if error_code.endswith("coverage_incomplete")
                        else "锚点评估模型当前不可用，请稍后从失败位置重试。"
                    ),
                    error_category=batch.error_category
                    or StepErrorCategory.EXTERNAL_PERMANENT,
                    recovery_action=RecoveryAction.USER_RETRY,
                )
            for value in batch.results.values():
                value_rows = [
                    dict(item)
                    for item in value.get("judgements") or ()
                    if isinstance(item, dict)
                ]
                rows.extend(value_rows)
                expected_pair_count += int(value.get("expectedPairCount") or 0)
                valid_pair_count += int(value.get("validPairCount") or len(value_rows))
                missing_pairs.extend(
                    dict(item)
                    for item in value.get("missingPairs") or ()
                    if isinstance(item, dict)
                )
        if expected_pair_count == 0:
            return StepOutcome.failed(
                error_code="post_interview_topology_anchor_route_missing",
                error_message="本轮能力证据无法映射到上一版评分锚点，请先重建评估来源。",
                error_category=StepErrorCategory.VALIDATION,
                recovery_action=RecoveryAction.REVIEW_REQUIRED,
            )
        if missing_pairs or valid_pair_count != expected_pair_count:
            # 有效行已经保存在各批次 Activity Artifact 中供审计，但矩阵不完整时
            # 不能进入评分和发布；用户重试只会从当前失败步骤继续。
            return StepOutcome.failed(
                error_code="post_interview_anchor_coverage_incomplete",
                error_message=(
                    f"锚点判断仅完成 {valid_pair_count}/{expected_pair_count}，"
                    "请从失败位置重新计算。"
                ),
                error_category=StepErrorCategory.VALIDATION,
                recovery_action=RecoveryAction.USER_RETRY,
            )
        evidence = IncrementalEvidence(
            affected_result_refs=(),
            unchanged_result_refs=(),
            evidence_index={},
            scoring_evidence={"anchor_judgements": rows},
            anchor_judgements=tuple(rows),
            current_interview_record_ids=tuple(
                source.source_manifest.interview_record_ids
            ),
        )
        data = {
            "judgements": rows,
            "evidence": evidence.as_dict(),
            "noScorableEvidence": False,
            "allNotSupport": bool(rows)
            and all(str(item.get("judgement") or "") == "not_support" for item in rows),
            "coverage": {
                "complete": True,
                "expectedPairCount": expected_pair_count,
                "validPairCount": valid_pair_count,
            },
        }
        return StepOutcome.succeeded(data=data)

    def score(context: Any) -> StepOutcome:
        # 评分 Step 只消费冻结拓扑、标准化断言和锚点判断；不再发现节点，
        # 也不从当前 ResumeProfile/JD 扩展评分范围。
        store = WorkflowArtifactStore()
        with SessionLocal() as db:
            parsed = store.get_json(
                db, context.previous_output_refs["parse_interview_units"]["artifactId"]
            )
            judged = store.get_json(
                db,
                context.previous_output_refs["evaluate_anchor_judgements"][
                    "artifactId"
                ],
            )
        assertions = _build_scoring_assertions(parsed, judged)
        source = _frozen_source(context)
        meaningful_count = sum(
            1
            for row in judged.get("judgements") or ()
            if isinstance(row, dict) and str(row.get("judgement") or "") != "not_support"
        )
        if meaningful_count and not assertions:
            return StepOutcome.failed(
                error_code="post_interview_anchor_updates_missing",
                error_message="面评已形成有效判断，但未能绑定到评分更新，请从本步骤重试。",
                error_category=StepErrorCategory.VALIDATION,
                recovery_action=RecoveryAction.USER_RETRY,
            )
        # 此纯算法步骤没有可安全降级的异常类型。异常意味着冻结拓扑或评分合同
        # 已不可信，不能伪造一份 +0 版本；交由 StepRunner 记录失败并提供用户重试。
        try:
            result = run_topology_incremental_scoring(
                frozen_topology=source.previous_topology,
                previous_core_result=source.previous_core_result,
                assertions=assertions,
            )
        except (RuntimeError, ValueError) as error:
            outcome = _source_contract_failure(error)
            if outcome is not None:
                return outcome
            raise
        if meaningful_count and not result.updates:
            return StepOutcome.failed(
                error_code="post_interview_anchor_updates_missing",
                error_message="面评判断未能绑定到冻结评分锚点，请重新解析本轮面评。",
                error_category=StepErrorCategory.VALIDATION,
                recovery_action=RecoveryAction.USER_RETRY,
            )
        # 锚点判断可能与上一版分数相同，例如 partial 和基线都为 0.60。
        # 这是合法的本轮 +0 结论，必须继续发布，不能把无净变化误判为流程失败。
        return StepOutcome.succeeded(
            data={
                "topologyResult": topology_result_as_dict(result),
                "coreResult": dict(result.core_result),
                "anchorEvaluation": {
                    "noScorableEvidence": bool(judged.get("noScorableEvidence")),
                    "allNotSupport": bool(judged.get("allNotSupport")),
                },
            }
        )

    def derive_rules(context: Any) -> StepOutcome:
        source = _frozen_source(context)
        store = WorkflowArtifactStore()
        with SessionLocal() as db:
            scoring = store.get_json(
                db,
                context.previous_output_refs["run_topology_incremental_scoring"][
                    "artifactId"
                ],
            )
            judged = store.get_json(
                db,
                context.previous_output_refs["evaluate_anchor_judgements"][
                    "artifactId"
                ],
            )
        from backend.app.modules.assessment.services.incremental_rule_derivation_service import (
            IncrementalRuleDerivationService,
        )
        from recruitment_ai_core.incremental_scoring import IncrementalScoringResult

        # 规则结果与本轮核心评分必须原子对应；未知错误时复用旧 Signal 会把
        # 本轮 Target 关闭等变化静默丢失，因此交由运行时进入用户可恢复的失败态。
        try:
            result = IncrementalRuleDerivationService().derive(
                source=source,
                core_result=IncrementalScoringResult.from_dict(
                    dict(scoring.get("coreResult") or {})
                ),
                evidence=IncrementalEvidence.from_dict(
                    dict(judged.get("evidence") or {})
                ),
            )
        except (RuntimeError, ValueError) as error:
            outcome = _source_contract_failure(error)
            if outcome is not None:
                return outcome
            raise
        return StepOutcome.succeeded(data={"ruleResult": dict(result)})

    def presentation(context: Any) -> StepOutcome:
        source = _frozen_source(context)
        store = WorkflowArtifactStore()
        with SessionLocal() as db:
            scoring = store.get_json(
                db,
                context.previous_output_refs["run_topology_incremental_scoring"][
                    "artifactId"
                ],
            )
            rules = store.get_json(
                db,
                context.previous_output_refs["derive_incremental_rules"]["artifactId"],
            )
        from recruitment_ai_core.incremental_scoring import IncrementalScoringResult

        service = PostInterviewActivityService()
        core = IncrementalScoringResult.from_dict(dict(scoring.get("coreResult") or {}))
        rule_result = dict(rules.get("ruleResult") or {})
        prepared = service.prepare_presentation(
            source=source, core_result=core, rule_result=rule_result
        )
        evaluation = dict(scoring.get("anchorEvaluation") or {})
        prepared["roundEvidenceStatus"] = (
            "no_scorable_evidence"
            if evaluation.get("noScorableEvidence")
            else "all_not_support" if evaluation.get("allNotSupport") else "evaluated"
        )
        batch = service.generate_presentation_bundle(
            context,
            source=source,
            core_result=core,
            rule_result=rule_result,
            presentation_input=prepared,
        )
        if not batch.is_usable:
            # 只有 ActivityRunner 认定为安全的模型边界错误才会产出 degraded
            # Bundle。这里不能再次吞掉任何失败，否则 RuntimeError 仍会被伪装为
            # 正常发布的展示产物。
            if batch.status == "retry_wait":
                return StepOutcome.activity_retry_wait(
                    error_code=batch.error_code
                    or "incremental_presentation_retry_wait",
                    error_message=batch.error_message or "展示生成正在等待重试",
                    retry_after_seconds=max(1, int(batch.retry_after_seconds or 15)),
                )
            return StepOutcome.failed(
                error_code=batch.error_code or "incremental_presentation_failed",
                error_message=batch.error_message or "展示生成失败",
                error_category=batch.error_category
                or StepErrorCategory.EXTERNAL_PERMANENT,
            )
        bundle = dict(
            batch.results["incremental_presentation_bundle"].get("bundle") or {}
        )
        return StepOutcome.succeeded(
            data=service.assemble_presentation(
                source=source,
                core_result=core,
                rule_result=rule_result,
                presentation_input=prepared,
                bundle=bundle,
            )
        )

    def delegate(name: str):
        return lambda context: handlers[name].handler(context)

    def persist(name: str):
        return lambda db, context, outcome: handlers[name].persist_success(
            db, context, outcome
        )

    return WorkflowSpec(
        workflow_type=workflow_type,
        handler=None,
        transition_handler=transition_handler,
        blocked_handler=blocked_handler,
        definition_version=POST_INTERVIEW_TOPOLOGY_DEFINITION_VERSION,
        steps=(
            StepDefinition(
                STEP_NAMES[0],
                1,
                delegate("freeze_post_interview_sources"),
                factory,
                handlers["freeze_post_interview_sources"].policy,
                persist("freeze_post_interview_sources"),
                artifact_type=f"{stage.value}_source_manifest",
            ),
            StepDefinition(
                STEP_NAMES[1],
                2,
                parse_units,
                factory,
                StepPolicy(
                    timeout_seconds=300,
                    max_attempts=2,
                    total_deadline_seconds=900,
                    external=True,
                ),
                _persist_json(f"{stage.value}_interview_assertions"),
                artifact_type=f"{stage.value}_interview_assertions",
            ),
            StepDefinition(
                STEP_NAMES[2],
                3,
                evaluate,
                factory,
                StepPolicy(
                    timeout_seconds=240,
                    max_attempts=2,
                    total_deadline_seconds=600,
                    external=True,
                ),
                _persist_json(f"{stage.value}_anchor_judgements"),
                artifact_type=f"{stage.value}_anchor_judgements",
            ),
            StepDefinition(
                STEP_NAMES[3],
                4,
                score,
                factory,
                StepPolicy(
                    timeout_seconds=120, max_attempts=2, total_deadline_seconds=300
                ),
                _persist_json(f"{stage.value}_topology_core_result"),
                artifact_type=f"{stage.value}_topology_core_result",
            ),
            StepDefinition(
                STEP_NAMES[4],
                5,
                derive_rules,
                factory,
                StepPolicy(
                    timeout_seconds=120, max_attempts=1, total_deadline_seconds=240
                ),
                _persist_json(f"{stage.value}_incremental_rule_result"),
                artifact_type=f"{stage.value}_incremental_rule_result",
            ),
            StepDefinition(
                STEP_NAMES[5],
                6,
                presentation,
                factory,
                StepPolicy(
                    timeout_seconds=240,
                    max_attempts=2,
                    total_deadline_seconds=600,
                    external=True,
                ),
                _persist_json(f"{stage.value}_incremental_presentation"),
                artifact_type=f"{stage.value}_incremental_presentation",
            ),
            StepDefinition(
                STEP_NAMES[6],
                7,
                delegate("publish_post_interview_assessment"),
                factory,
                handlers["publish_post_interview_assessment"].policy,
                persist("publish_post_interview_assessment"),
            ),
        ),
    )


__all__ = ["STEP_NAMES", "build_post_interview_scoring_spec"]
