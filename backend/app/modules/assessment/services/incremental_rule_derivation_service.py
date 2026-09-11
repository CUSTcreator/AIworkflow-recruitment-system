"""V2/V3 确定性业务规则派生服务。"""
from __future__ import annotations

from typing import Any, Mapping

from backend.app.modules.assessment.domain.assessment_version import AssessmentRuleResult
from backend.app.modules.assessment.services.source_service import AssessmentSource
from recruitment_ai_core.assessment_rules import derive_assessment_rules
from recruitment_ai_core.incremental_scoring import IncrementalEvidence, IncrementalScoringResult


class IncrementalRuleDerivationService:
    """把本轮原始评分转换为当前 Signal、前后并集与 Target open/resolved 更新。"""

    def derive(
        self,
        *,
        source: AssessmentSource,
        core_result: IncrementalScoringResult,
        evidence: IncrementalEvidence,
    ) -> Mapping[str, Any]:
        """只适配冻结输入；所有确定性规则必须委托算法包。

        V1、V2、V3 的 Strength/Weakness、推荐和 Target 候选共用同一算法入口。
        后端不得按分数自行截断或重新解释强弱，否则跨版本的新增/保持/关闭会失真。
        """
        # 只能使用面评解析器明确给出的稳定 Target ID。不能拿底层 capability/indicator ID
        # 对应 Observation 反查，否则多个 Target 指向同一能力时会被一并误关闭。
        open_target_ids = {
            str(item.get("interview_target_id") or "")
            for item in source.open_targets
            if str(item.get("interview_target_id") or "")
        }
        resolved_target_ids = {
            target_id
            for target_id in evidence.resolved_interview_target_ids
            if target_id in open_target_ids
        }
        previous_rule = {
            **dict(source.previous_rule_result or {}),
            "assessment_version_id": str(
                source.previous_assessment.get("assessment_version_id") or ""
            ),
        }
        result = derive_assessment_rules(
            stage=source.stage.value,
            core_result=core_result.as_dict(),
            job_profile=source.job_requirement_profile,
            application_id=source.application_id,
            previous_rule_result=previous_rule,
            open_targets=[dict(item) for item in source.open_targets],
            resolved_interview_target_ids=resolved_target_ids,
        )
        return AssessmentRuleResult.model_validate(result).model_dump(mode="json")