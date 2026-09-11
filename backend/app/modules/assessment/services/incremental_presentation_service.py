"""V2/V3 展示加工服务。"""
from __future__ import annotations

from typing import Any, Mapping

from backend.app.modules.assessment.domain.assessment_version import AssessmentRuleResult
from backend.app.modules.assessment.services.presentation_generation_service import PresentationGenerationService
from backend.app.modules.assessment.services.source_service import AssessmentSource
from recruitment_ai_core.incremental_scoring import IncrementalScoringResult


class IncrementalPresentationService:
    """V2/V3 只保留一个展示 Bundle：推荐由后端冻结，LLM 生成三块页面文案。"""

    def __init__(self, service: PresentationGenerationService | None = None) -> None:
        self._service = service or PresentationGenerationService()

    def prepare_input(self, *, source: AssessmentSource, core_result: IncrementalScoringResult, rule_result: Mapping[str, Any]) -> Mapping[str, Any]:
        """构造冻结展示输入；仅供单个 Bundle Activity 重试与恢复。"""
        return self._service.prepare_incremental_input(source=source, core_result=core_result, rule_result=AssessmentRuleResult.model_validate(rule_result))

    def generate_bundle(self, *, stage: str, presentation_input: Mapping[str, Any]) -> Mapping[str, Any]:
        """执行唯一 LLM 活动，返回优势、薄弱项与核验重点文案。"""
        return self._service.generate_incremental_bundle(stage=stage, presentation_input=presentation_input)

    def assemble(self, *, source: AssessmentSource, core_result: IncrementalScoringResult, rule_result: Mapping[str, Any], presentation_input: Mapping[str, Any], bundle: Mapping[str, Any]) -> Mapping[str, Any]:
        """按冻结顺序绑定 Bundle，不再存在摘要/核验重点两个独立产物。"""
        return self._service.assemble_incremental_presentation(source=source, core_result=core_result, rule_result=AssessmentRuleResult.model_validate(rule_result), presentation_input=presentation_input, bundle=bundle).model_dump(mode="json")

    def fallback_bundle(self, *, presentation_input: Mapping[str, Any]) -> Mapping[str, Any]:
        """V2/V3 展示 Activity 的本地规则化回退。"""
        return self._service.fallback_incremental_bundle(presentation_input=presentation_input)

    def generate(self, *, source: AssessmentSource, core_result: IncrementalScoringResult, rule_result: Mapping[str, Any]) -> Mapping[str, Any]:
        """同步入口；当前 V2/V3 工作流使用该入口。"""
        return self._service.generate(source=source, core_result=core_result, rule_result=AssessmentRuleResult.model_validate(rule_result)).model_dump(mode="json")
