"""评估展示生成：后端冻结推荐程度，LLM 只生成具体页面文案。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Mapping

from backend.app.infrastructure.llm.presentation_gateway import PresentationLlmGateway
from backend.app.modules.assessment.domain.assessment_version import (
    AssessmentPresentationResult,
    AssessmentRuleResult,
    InterviewTargetStatus,
    PresentationSignal,
    RoundChangeSummary,
    VerificationFocus,
)
from recruitment_ai_core.decision_summary.assessment_presentation_input import (
    build_assessment_presentation_inputs,
)
from recruitment_ai_core.decision_summary.fallback import build_summary_text
from recruitment_ai_core.screening_scoring.result_contracts import (
    PresentationResult,
    RuleDerivedResult,
)


class PresentationGenerationService:
    """展示层不改变分数、Signal、Target 状态、证据 ID 或任何流程状态。"""

    def __init__(self, gateway: PresentationLlmGateway | None = None) -> None:
        self.gateway = gateway or PresentationLlmGateway()

    def prepare_incremental_input(
        self, *, source: Any, core_result: Any, rule_result: AssessmentRuleResult
    ) -> dict[str, Any]:
        """构造 V2/V3 单个 Bundle LLM 活动的只读输入。

        这是纯数据转换：它不调用模型，也不写数据库。V2/V3 与 V1 使用同一个
        Bundle 合同，因此推荐程度、优势、薄弱项和核验重点可以一次生成并统一降级。
        """
        rule = (
            rule_result
            if isinstance(rule_result, AssessmentRuleResult)
            else AssessmentRuleResult.model_validate(rule_result)
        )
        return build_assessment_presentation_inputs(
            stage=source.stage.value,
            core_result=_core_value(core_result),
            rule_result=rule.model_dump(mode="json"),
            job_profile=dict(source.job_requirement_profile),
            interview_targets=[
                item.model_dump(mode="json") for item in rule.target_updates
            ],
        )

    def generate_incremental_bundle(
        self,
        *,
        presentation_input: Mapping[str, Any],
        stage: str,
    ) -> dict[str, Any]:
        """V2/V3 的单个展示 Activity：一次调用同时生成推荐和全部页面文案。"""
        content, generation_mode = self.gateway.generate_bundle(
            stage=stage,
            bundle_input=dict(presentation_input["bundle_input"]),
        )
        return {"content": content, "generationMode": generation_mode}

    def assemble_incremental_presentation(
        self,
        *,
        source: Any,
        core_result: Any,
        rule_result: AssessmentRuleResult,
        presentation_input: Mapping[str, Any],
        bundle: Mapping[str, Any],
    ) -> AssessmentPresentationResult:
        """将单次 LLM Activity 的输出按冻结顺序回填，不再读取规则 triggerCode。"""
        rule = (
            rule_result
            if isinstance(rule_result, AssessmentRuleResult)
            else AssessmentRuleResult.model_validate(rule_result)
        )
        prepared, content = dict(presentation_input), dict(bundle.get("content") or {})
        mode = str(bundle.get("generationMode") or "rule_fallback")
        bundle_input = dict(prepared.get("bundle_input") or {})
        return AssessmentPresentationResult(
            recommendation_level=str(dict(bundle_input.get("recommendation") or {}).get("level") or "cautious_recommend"),
            recommendation_reason=build_summary_text(bundle_input, content.get("summary")),
            strengths=_presentation_signals(
                content.get("strengths"),
                {
                    item["signal_id"]: prepared["signal_context"][item["signal_id"]]
                    for item in prepared["summary_input"].get("strength_signals", [])
                    if item["signal_id"] in prepared["signal_context"]
                },
            ),
            weaknesses=_presentation_signals(
                content.get("weaknesses"),
                {
                    item["signal_id"]: prepared["signal_context"][item["signal_id"]]
                    for item in prepared["summary_input"].get("weakness_signals", [])
                    if item["signal_id"] in prepared["signal_context"]
                },
            ),
            verification_focus=_verification_focus(
                content.get("verification_focus"),
                rule.target_updates,
                prepared["target_context"],
            ),
            round_change_summary=_round_summary(
                source.stage.value,
                _list_attr(core_result, "score_changes"),
                _list_attr(core_result, "capability_changes"),
                evidence_status=str(prepared.get("roundEvidenceStatus") or "evaluated"),
            ),
            generation_mode=mode,
            summary_generation_mode=mode,
            verification_focus_generation_mode=mode,
            source_input_hash=_hash(
                {
                    "source": source.source_manifest.source_input_hash,
                    "rule": rule.model_dump(mode="json"),
                }
            ),
        )

    def generate(
        self, *, source: Any, core_result: Any, rule_result: AssessmentRuleResult
    ) -> AssessmentPresentationResult:
        """生成 V2/V3 展示结果，但不让规则层内部 ID 泄漏到页面。

        ``build_assessment_presentation_inputs`` 是唯一的规则事实→展示输入转换点：
        它从冻结 JobRequirementProfile 和来源结果补齐中文名称、定义、理由与证据。
        LLM 仅能改写这些事实；失败时同一输入也供确定性模板安全降级。
        """
        rule = (
            rule_result
            if isinstance(rule_result, AssessmentRuleResult)
            else AssessmentRuleResult.model_validate(rule_result)
        )
        core = _core_value(core_result)
        prepared = build_assessment_presentation_inputs(
            stage=source.stage.value,
            core_result=core,
            rule_result=rule.model_dump(mode="json"),
            job_profile=dict(source.job_requirement_profile),
            interview_targets=[
                item.model_dump(mode="json") for item in rule.target_updates
            ],
        )
        # V2/V3 与 V1 共用一个展示 Bundle 调用：模型读取四项冻结评分和事实材料，
        # 只产出推荐程度与页面文案，不能修改底层评分或领域对象。
        bundle, generation_mode = self.gateway.generate_bundle(
            stage=source.stage.value,
            bundle_input=dict(prepared["bundle_input"]),
        )
        content = dict(bundle or {})
        bundle_input = dict(prepared.get("bundle_input") or {})
        strengths = _presentation_signals(
            bundle.get("strengths"),
            {
                item["signal_id"]: prepared["signal_context"][item["signal_id"]]
                for item in prepared["summary_input"].get("strength_signals", [])
                if item["signal_id"] in prepared["signal_context"]
            },
        )
        weaknesses = _presentation_signals(
            bundle.get("weaknesses"),
            {
                item["signal_id"]: prepared["signal_context"][item["signal_id"]]
                for item in prepared["summary_input"].get("weakness_signals", [])
                if item["signal_id"] in prepared["signal_context"]
            },
        )
        verification = _verification_focus(
            bundle.get("verification_focus"),
            rule.target_updates,
            prepared["target_context"],
        )
        score_changes = _list_attr(core_result, "score_changes")
        capability_changes = _list_attr(core_result, "capability_changes")
        summary = _round_summary(source.stage.value, score_changes, capability_changes)
        return AssessmentPresentationResult(
            recommendation_level=str(dict(bundle_input.get("recommendation") or {}).get("level") or "cautious_recommend"),
            recommendation_reason=build_summary_text(bundle_input, content.get("summary")),
            strengths=strengths,
            weaknesses=weaknesses,
            verification_focus=verification,
            round_change_summary=summary,
            generation_mode=generation_mode,
            summary_generation_mode=generation_mode,
            verification_focus_generation_mode=generation_mode,
            source_input_hash=_hash(
                {
                    "source": source.source_manifest.source_input_hash,
                    "rule": rule.model_dump(mode="json"),
                }
            ),
        )

    def generate_screening_bundle(
        self,
        *,
        rule: RuleDerivedResult,
        presentation_input: Mapping[str, Any],
    ) -> dict[str, Any]:
        """一次外部调用生成推荐结论及三块展示文案。"""
        prepared = dict(presentation_input)
        content, generation_mode = self.gateway.generate_bundle(
            stage="screening",
            bundle_input=dict(prepared["bundle_input"]),
        )
        return {"content": content, "generationMode": generation_mode}

    def fallback_screening_bundle(
        self,
        *,
        rule: RuleDerivedResult,
        presentation_input: Mapping[str, Any],
    ) -> dict[str, Any]:
        """展示 Activity 重试耗尽后的确定性结果，不触发 LLM。"""
        from recruitment_ai_core.decision_summary.fallback import (
            build_fallback_presentation_bundle,
        )

        prepared = dict(presentation_input)
        return {
            "content": build_fallback_presentation_bundle(
                dict(prepared["bundle_input"])
            ),
            "generationMode": "rule_fallback",
        }

    def fallback_incremental_bundle(
        self,
        *,
        presentation_input: Mapping[str, Any],
    ) -> dict[str, Any]:
        """V2/V3 展示活动耗尽后的确定性回退，不再次调用模型。"""
        from recruitment_ai_core.decision_summary.fallback import (
            build_fallback_presentation_bundle,
        )

        return {
            "content": build_fallback_presentation_bundle(
                dict(presentation_input.get("bundle_input") or {})
            ),
            "generationMode": "rule_fallback",
        }

    @staticmethod
    def assemble_screening_bundle(
        *,
        rule: RuleDerivedResult,
        bundle: Mapping[str, Any],
        presentation_input: Mapping[str, Any],
    ) -> PresentationResult:
        """按冻结输入顺序回填 Signal/Target 与证据，模型从不参与业务对象关联。"""
        prepared = dict(presentation_input)
        content = dict(bundle.get("content") or {})
        mode = str(bundle.get("generationMode") or "rule_fallback")
        bundle_input = dict(prepared.get("bundle_input") or {})
        return PresentationResult(
            recommendation_level=str(dict(bundle_input.get("recommendation") or {}).get("level") or "cautious_recommend"),
            recommendation_reason=build_summary_text(bundle_input, content.get("summary")),
            strengths=_attached_signal_groups(
                content.get("strengths") or [],
                {
                    item["signal_id"]: prepared["signal_context"][item["signal_id"]]
                    for item in prepared["summary_input"].get("strength_signals", [])
                    if item["signal_id"] in prepared["signal_context"]
                },
            ),
            weaknesses=_attached_signal_groups(
                content.get("weaknesses") or [],
                {
                    item["signal_id"]: prepared["signal_context"][item["signal_id"]]
                    for item in prepared["summary_input"].get("weakness_signals", [])
                    if item["signal_id"] in prepared["signal_context"]
                },
            ),
            verification_focus=_attached_target_groups(
                content.get("verification_focus") or [],
                prepared["target_context"],
            ),
            generation_mode=mode,
            summary_generation_mode=mode,
            verification_focus_generation_mode=mode,
            generated_at=datetime.now(timezone.utc).isoformat(),
            source_input_hash=_hash(
                {
                    "rule": _rule_payload(rule),
                    "signals": sorted(prepared["signal_context"]),
                }
            ),
        )


def _attached_signal_groups(
    groups: list[dict[str, Any]], contexts: Mapping[str, Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """按模型返回的 input_index 回填；无法确认位置时使用本地模板。"""
    output: list[dict[str, Any]] = []
    values = list(contexts.values())
    indexed_groups = _groups_by_input_index(groups)
    for index, context in enumerate(values, start=1):
        group = indexed_groups.get(index, {})
        output.append(
            {
                "item_id": f"SUMMARY_{index:02d}",
                "signal_key": str(context.get("signal_id") or ""),
                "title": str(
                    group.get("title") or context.get("target_name") or "能力表现"
                ),
                "summary": str(
                    group.get("summary")
                    or context.get("result_summary")
                    or "当前结果缺少可展示的具体证据说明。"
                ),
                "evidence_ids": list(context.get("evidence_ids") or []),
            }
        )
    return output


def _attached_target_groups(
    groups: list[dict[str, Any]], contexts: Mapping[str, Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """按模型返回的 input_index 回填核验重点；缺项仅降级为规则文本。"""
    output: list[dict[str, Any]] = []
    values = list(contexts.values())
    indexed_groups = _groups_by_input_index(groups)
    for index, context in enumerate(values, start=1):
        group = indexed_groups.get(index, {})
        output.append(
            {
                "focus_id": f"FOCUS_{index:02d}",
                "target_id": str(context.get("interview_target_id") or ""),
                "status": str(context.get("status") or "open"),
                "title": str(
                    group.get("title") or context.get("target_name") or "待核验能力"
                ),
                "reason": str(
                    group.get("reason")
                    or context.get("source_result_summary")
                    or "该项需要在本轮进一步确认。"
                ),
                "goal": str(
                    group.get("verification_goal")
                    or context.get("verification_goal")
                    or "确认相关事实和候选人的实际贡献。"
                ),
                "evidence_ids": list(context.get("evidence_ids") or []),
            }
        )
    return output


def _groups_by_input_index(
    groups: list[dict[str, Any]]
) -> dict[int, Mapping[str, Any]]:
    """只信任唯一、正整数位置；旧/异常响应不会造成跨项目的文案错绑。"""
    output: dict[int, Mapping[str, Any]] = {}
    for group in groups:
        if not isinstance(group, Mapping):
            continue
        position = group.get("input_index")
        if (
            isinstance(position, bool)
            or not isinstance(position, int)
            or position < 1
            or position in output
        ):
            continue
        output[position] = group
    return output


def _presentation_signals(
    groups: Any, contexts: Mapping[str, Mapping[str, Any]]
) -> list[PresentationSignal]:
    rows = _attached_signal_groups(list(groups or []), contexts)
    return [
        PresentationSignal(
            signal_key=str(row["signal_key"]),
            title=str(row["title"]),
            summary=str(row["summary"]),
            evidence_ids=list(row["evidence_ids"]),
        )
        for row in rows
    ]


def _verification_focus(
    groups: Any, target_updates: list[Any], contexts: Mapping[str, Mapping[str, Any]]
) -> list[VerificationFocus]:
    del target_updates
    rows = _attached_target_groups(list(groups or []), contexts)
    return [
        VerificationFocus(
            target_id=str(row["target_id"]),
            status=InterviewTargetStatus(str(row["status"])),
            title=str(row["title"]),
            reason=str(row["reason"]),
            goal=str(row["goal"]),
            evidence_ids=list(row["evidence_ids"]),
        )
        for row in rows
    ]


def _round_summary(
    stage: str,
    score_changes: list[Any],
    capability_changes: list[Any],
    *,
    evidence_status: str = "evaluated",
) -> RoundChangeSummary:
    label = "一面后变化" if stage == "after_first_interview" else "二面后变化"
    summary = {
        "no_scorable_evidence": "本轮面评未形成可评分的能力证据，评分保持不变。",
        "all_not_support": "本轮新增面评未支持或改变已有评分锚点，评分保持不变。",
    }.get(evidence_status, "本轮评估已根据新增面评事实更新。")
    return RoundChangeSummary(
        title=label,
        summary=summary,
        score_changes=list(score_changes or []),
        capability_changes=list(capability_changes or []),
    )


def _core_value(value: Any) -> dict[str, Any]:
    if hasattr(value, "as_dict"):
        return dict(value.as_dict())
    if hasattr(value, "model_dump"):
        return dict(value.model_dump(mode="json"))
    return dict(value) if isinstance(value, Mapping) else {}


def _list_attr(value: Any, name: str) -> list[Any]:
    if isinstance(value, Mapping):
        return list(value.get(name) or [])
    return list(getattr(value, name, None) or [])


def _rule_payload(rule: RuleDerivedResult) -> dict[str, Any]:
    return {
        "strength_signals": list(rule.strength_signals),
        "weakness_signals": list(rule.weakness_signals),
        "target_updates": list(rule.target_updates),
    }


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode(
            "utf-8"
        )
    ).hexdigest()
