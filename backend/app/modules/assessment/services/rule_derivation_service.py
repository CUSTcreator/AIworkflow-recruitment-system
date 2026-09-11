"""评估规则派生：生成当前 Top 4、前后变化并集和 Target 状态更新。"""
from __future__ import annotations

from typing import Any, Mapping

from backend.app.modules.assessment.domain.assessment_version import AssessmentRuleResult
from recruitment_ai_core.assessment_rules import derive_assessment_rules
from recruitment_ai_core.decision_summary.input_builder import evidence_ids_for_result_ids
from recruitment_ai_core.screening_scoring.result_contracts import RuleDerivedResult, ScreeningCoreResult




class RuleDerivationService:
    """规则层只依据核心结果和已确认观察，不调用 LLM。"""

    def __init__(self, *, application_id: str | None = None) -> None:
        self.application_id = application_id

    def derive(self, *, source: Any, core_result: Any, scoring_evidence: Any) -> AssessmentRuleResult:
        """V2/V3 兼容入口：只适配来源，规则判断统一委托算法包。

        该入口保留是为了不破坏历史调用方；它不能再包含“按分数取前四/后四”之类
        的后端业务判断。正式 V2/V3 Workflow 当前使用更窄的
        ``IncrementalRuleDerivationService``，两者最终调用同一个纯规则函数。
        """
        core = _core_dict(core_result)
        evidence = _mapping(
            scoring_evidence.as_dict() if hasattr(scoring_evidence, "as_dict") else scoring_evidence
        )
        # 兼容入口也必须遵循同一语义：只有面评解析显式给出的稳定 Target ID 才能
        # 关闭事项，不能按底层能力 ID 反查 Observation，否则会误关闭同能力的多个事项。
        open_target_ids = {
            str(target.get("interview_target_id") or "")
            for target in source.open_targets
            if str(target.get("interview_target_id") or "")
        }
        resolved_target_ids = {
            str(target_id)
            for target_id in evidence.get("resolved_interview_target_ids") or []
            if str(target_id) in open_target_ids
        }
        previous = {
            **_mapping(source.previous_rule_result),
            "assessment_version_id": str(
                source.previous_assessment.get("assessment_version_id") or ""
            ),
        }
        raw = derive_assessment_rules(
            stage=source.stage.value,
            core_result=core,
            job_profile=dict(source.job_requirement_profile),
            application_id=str(source.application_id),
            previous_rule_result=previous,
            open_targets=[dict(item) for item in source.open_targets],
            resolved_interview_target_ids=resolved_target_ids,
        )
        return AssessmentRuleResult.model_validate(raw)

    def derive_screening(self, *, core: ScreeningCoreResult, hard_screening_result: dict[str, Any]) -> RuleDerivedResult:
        """V1 也必须通过算法包统一规则入口，不能保留独立解释逻辑。

        这里仅做 V1 运行时契约适配：补入硬筛结论、将来源结果 ID 映射为证据 ID。
        Strength/Weakness、推荐和首次 InterviewTarget 候选均由
        ``derive_assessment_rules`` 产生，因此与 V2/V3 的业务规则完全一致。
        """
        score = {
            **dict(core.score_result),
            "qualificationStatus": hard_screening_result.get("gate", "unclear"),
        }
        raw = derive_assessment_rules(
            stage="screening",
            core_result={
                "preset_experience_result": dict(core.preset_experience_result),
                "job_result": dict(core.job_result),
                "score_result": score,
                "capability_graph": dict(core.capability_graph),
            },
            job_profile=dict(core.job_profile),
            application_id=str(self.application_id or ""),
        )
        return RuleDerivedResult(
            hard_screening_result=dict(hard_screening_result),
            strength_signals=_bind_signal_evidence(list(raw["strength_signals"]), core),
            weakness_signals=_bind_signal_evidence(list(raw["weakness_signals"]), core),
            target_updates=_bind_target_evidence(list(raw["target_updates"]), core),
        )

def _core_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "as_dict"):
        return value.as_dict()
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return dict(value) if isinstance(value, Mapping) else {}



def _bind_signal_evidence(signals: list[dict[str, Any]], core: ScreeningCoreResult) -> list[dict[str, Any]]:
    output = []
    for item in signals:
        signal = dict(item)
        signal["signal_id"] = str(signal.get("signal_key") or signal.get("strength_signal_id") or signal.get("weakness_signal_id") or "")
        signal["evidence_ids"] = evidence_ids_for_result_ids(list(signal.get("source_result_ids") or []), core.capability_graph)
        output.append(signal)
    return output


def _bind_target_evidence(targets: list[dict[str, Any]], core: ScreeningCoreResult) -> list[dict[str, Any]]:
    output = []
    for item in targets:
        target = dict(item)
        target["title"] = _target_title(target, core.job_profile)
        target["evidence_ids"] = evidence_ids_for_result_ids(list(target.get("source_result_ids") or []), core.capability_graph)
        output.append(target)
    return output


def _target_title(target: Mapping[str, Any], job_profile: Mapping[str, Any]) -> str:
    """为正式 InterviewTarget 补齐面试官可读名称，算法包不承担展示名称拼装。"""
    target_id = str(target.get("target_id") or "")
    if str(target.get("target_type") or "") == "job_capability":
        for capability in job_profile.get("job_capabilities") or []:
            if isinstance(capability, Mapping) and str(capability.get("job_capability_id") or "") == target_id:
                return str(capability.get("capability_name") or capability.get("capability_definition") or target_id)
    return target_id or "待核验能力"


