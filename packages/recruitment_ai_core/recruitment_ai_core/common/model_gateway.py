from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from recruitment_ai_core.execution.model_call_context import ModelCallContext


@runtime_checkable
class ModelGateway(Protocol):
    """供算法包使用的、供应商无关的模型接口。"""

    def generate_json(
        self,
        *,
        workflow_name: str,
        messages: list[dict[str, str]],
        schema_name: str,
        json_schema: dict[str, Any] | None = None,
        local_validation_schema: dict[str, Any] | None = None,
        settings_overrides: dict[str, Any] | None = None,
        request_context: ModelCallContext | None = None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]: ...

    def embed(
        self,
        texts: list[str],
        *,
        settings_overrides: dict[str, Any] | None = None,
        request_context: ModelCallContext | None = None,
    ) -> tuple[list[list[float]], dict[str, Any]]: ...


class UnconfiguredModelGateway:
    """应用尚未安装基础设施时使用的安全默认实现。"""

    def generate_json(self, *, workflow_name: str, messages: list[dict[str, str]], schema_name: str, json_schema: dict[str, Any] | None = None, local_validation_schema: dict[str, Any] | None = None, settings_overrides: dict[str, Any] | None = None, request_context: ModelCallContext | None = None) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        from recruitment_ai_core.llm.config import load_llm_settings
        from recruitment_ai_core.llm.errors import LLMCallError

        settings = load_llm_settings(settings_overrides)
        trace = settings.trace(workflow_name)
        trace["schema_name"] = schema_name
        if not settings.is_enabled_for(workflow_name):
            trace["mode"] = "disabled"
            return None, trace
        raise LLMCallError("model_gateway_not_configured")

    def embed(self, texts: list[str], *, settings_overrides: dict[str, Any] | None = None, request_context: ModelCallContext | None = None) -> tuple[list[list[float]], dict[str, Any]]:
        from recruitment_ai_core.llm.errors import LLMCallError
        raise LLMCallError("model_gateway_not_configured")


_model_gateway: ModelGateway = UnconfiguredModelGateway()


def configure_model_gateway(gateway: ModelGateway) -> None:
    if not isinstance(gateway, ModelGateway):
        raise TypeError("model_gateway_contract_invalid")
    global _model_gateway
    _model_gateway = gateway


def get_model_gateway() -> ModelGateway:
    return _model_gateway
