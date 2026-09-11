from __future__ import annotations

import hashlib
import json
from typing import Any

from recruitment_ai_core.common.model_gateway import get_model_gateway
from recruitment_ai_core.execution.model_call_context import current_model_call_context

from .config import LLMSettings, WorkflowLLMSettings, load_llm_settings
from .errors import LLMCallError, LLMResponseError


def call_json_llm(*, workflow_name: str, messages: list[dict[str, str]], schema_name: str, settings_overrides: dict[str, Any] | None = None, json_schema: dict[str, Any] | None = None, local_validation_schema: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    fingerprint = hashlib.sha256(json.dumps({"messages": messages, "schema": json_schema, "local_validation_schema": local_validation_schema}, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    request_context = current_model_call_context(workflow_name=workflow_name, operation=schema_name, fingerprint=fingerprint)
    return get_model_gateway().generate_json(workflow_name=workflow_name, messages=messages, schema_name=schema_name, settings_overrides=settings_overrides, json_schema=json_schema, local_validation_schema=local_validation_schema, request_context=request_context)


def call_embeddings(texts: list[str], *, settings_overrides: dict[str, Any] | None = None) -> tuple[list[list[float]], dict[str, Any]]:
    fingerprint = hashlib.sha256(json.dumps(texts, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()
    request_context = current_model_call_context(workflow_name="embeddings", operation="embed", fingerprint=fingerprint)
    return get_model_gateway().embed(texts, settings_overrides=settings_overrides, request_context=request_context)


__all__ = ["LLMCallError", "LLMResponseError", "LLMSettings", "WorkflowLLMSettings", "call_json_llm", "call_embeddings", "load_llm_settings"]
