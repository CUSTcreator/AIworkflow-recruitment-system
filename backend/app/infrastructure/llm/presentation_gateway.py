"""展示层的 LLM 基础设施网关。"""
from __future__ import annotations

from typing import Any, Callable
from backend.app.shared.errors import ExternalServiceError

from recruitment_ai_core.decision_summary.fallback import (
    build_fallback_presentation_bundle,
)
from recruitment_ai_core.decision_summary.llm_adapter import (
    try_generate_presentation_bundle,
)
from recruitment_ai_core.decision_summary.validator import (
    validate_presentation_bundle,
)


class PresentationLlmGateway:
    """将展示服务与具体模型适配器隔离；失败时返回受规则约束的回退内容。"""

    def generate_bundle(
        self, *, stage: str, bundle_input: dict[str, Any],
    ) -> tuple[dict[str, Any], str]:
        """单次生成全部展示文案；模型失败时整体回退为规则模板。"""
        return self._generate(
            caller=lambda payload, **kwargs: try_generate_presentation_bundle(
                payload, stage=stage, **kwargs
            ),
            validator=validate_presentation_bundle,
            payload=bundle_input,
            fallback=lambda: build_fallback_presentation_bundle(bundle_input),
        )
    def _generate(
        self, *, caller: Callable[..., tuple[dict[str, Any] | None, dict[str, Any]]],
        validator: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
        payload: dict[str, Any], fallback: Callable[[], dict[str, Any]],
    ) -> tuple[dict[str, Any], str]:
        error = ""
        for _ in range(2):
            try:
                generated, _trace = caller(payload, settings_overrides=None, validation_error=error)
                if generated is None:
                    raise ValueError("presentation_llm_unavailable")
                return validator(generated, payload), "llm"
            except ExternalServiceError:
                raise
            except Exception as exc:
                error = f"{type(exc).__name__}:{str(exc)[:240]}"
        return fallback(), "rule_fallback"
