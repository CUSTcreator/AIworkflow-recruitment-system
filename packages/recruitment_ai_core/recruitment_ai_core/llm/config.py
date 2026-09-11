from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class WorkflowLLMSettings:
    enabled: bool = False
    model: str | None = None
    temperature: float | None = None
    timeout_seconds: int | None = None
    json_mode: bool | None = None
    top_p: float | None = None
    strict_json_schema: bool | None = None
    thinking_mode: str | None = None


@dataclass(slots=True)
class LLMSettings:
    enabled: bool = False
    provider: str = "openai_compatible"
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_model: str = ""
    embedding_batch_size: int = 10
    timeout_seconds: int = 60
    temperature: float = 0.1
    json_mode: bool = True
    top_p: float = 1.0
    # OpenAI-compatible 供应商并不都实现 json_schema response_format；调用方仍会
    # 使用 Schema 约束 Prompt，并在本地校验实际响应。
    strict_json_schema: bool = False
    thinking_mode: str | None = None
    fail_open: bool = True
    workflows: dict[str, WorkflowLLMSettings] = field(default_factory=dict)
    execution: dict[str, Any] = field(default_factory=dict)
    resume_experience: dict[str, Any] = field(default_factory=dict)
    job_capability: dict[str, Any] = field(default_factory=dict)
    document_parsing: dict[str, Any] = field(default_factory=dict)
    source_path: str | None = None

    def workflow(self, name: str) -> WorkflowLLMSettings:
        return self.workflows.get(name, WorkflowLLMSettings())

    def is_enabled_for(self, name: str) -> bool:
        return self.enabled and self.workflow(name).enabled

    def model_for(self, name: str) -> str:
        return self.workflow(name).model or self.model

    def temperature_for(self, name: str) -> float:
        value = self.workflow(name).temperature
        return self.temperature if value is None else value

    def timeout_for(self, name: str) -> int:
        value = self.workflow(name).timeout_seconds
        return self.timeout_seconds if value is None else value

    def json_mode_for(self, name: str) -> bool:
        value = self.workflow(name).json_mode
        return self.json_mode if value is None else value

    def top_p_for(self, name: str) -> float:
        value = self.workflow(name).top_p
        return self.top_p if value is None else value

    def strict_json_schema_for(self, name: str) -> bool:
        value = self.workflow(name).strict_json_schema
        return self.strict_json_schema if value is None else value

    def thinking_mode_for(self, name: str) -> str | None:
        value = self.workflow(name).thinking_mode
        mode = self.thinking_mode if value is None else value
        if mode is None:
            return None
        normalized = str(mode).strip().lower()
        if normalized not in {"enabled", "disabled"}:
            raise ValueError(f"invalid_thinking_mode:{mode}")
        return normalized

    def trace(self, workflow_name: str) -> dict[str, Any]:
        return {
            "enabled": self.is_enabled_for(workflow_name),
            "provider": self.provider,
            "base_url": self.base_url,
            "model": self.model_for(workflow_name),
            "temperature": self.temperature_for(workflow_name),
            "json_mode": self.json_mode_for(workflow_name),
            "top_p": self.top_p_for(workflow_name),
            "strict_json_schema": self.strict_json_schema_for(workflow_name),
            "thinking_mode": self.thinking_mode_for(workflow_name),
            "config_source": self.source_path,
        }

    def embedding_trace(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "base_url": self.embedding_base_url or self.base_url,
            "model": self.embedding_model,
            "config_source": self.source_path,
        }


def load_llm_settings(overrides: dict[str, Any] | None = None) -> LLMSettings:
    payload = _load_config_payload()
    if overrides:
        payload = _deep_merge(payload, overrides)
    payload = _apply_env_overrides(payload)
    workflows = {
        name: WorkflowLLMSettings(**value)
        for name, value in (payload.get("workflows") or {}).items()
        if isinstance(value, dict)
    }
    return LLMSettings(
        enabled=_bool(payload.get("enabled"), False),
        provider=str(payload.get("provider") or "openai_compatible"),
        base_url=str(payload.get("base_url") or ""),
        api_key=str(payload.get("api_key") or ""),
        model=str(payload.get("model") or ""),
        embedding_base_url=str(payload.get("embedding_base_url") or payload.get("base_url") or ""),
        embedding_api_key=str(payload.get("embedding_api_key") if "embedding_api_key" in payload else payload.get("api_key") or ""),
        embedding_model=str(payload.get("embedding_model") or ""),
        embedding_batch_size=max(1, int(payload.get("embedding_batch_size") or 10)),
        timeout_seconds=int(payload.get("timeout_seconds") or 60),
        temperature=float(payload.get("temperature") if payload.get("temperature") is not None else 0.1),
        json_mode=_bool(payload.get("json_mode"), True),
        top_p=float(payload.get("top_p") if payload.get("top_p") is not None else 1.0),
        strict_json_schema=_bool(payload.get("strict_json_schema"), False),
        thinking_mode=(
            str(payload["thinking_mode"])
            if payload.get("thinking_mode") is not None
            else None
        ),
        fail_open=_bool(payload.get("fail_open"), True),
        workflows=workflows,
        execution=dict(payload.get("execution") or {}),
        resume_experience=dict(payload.get("resume_experience") or {}),
        job_capability=dict(payload.get("job_capability") or {}),
        document_parsing=dict(payload.get("document_parsing") or {}),
        source_path=payload.get("_source_path"),
    )


def _load_config_payload() -> dict[str, Any]:
    explicit_path = os.getenv("RECRUIT_LLM_CONFIG_PATH")
    candidates = [Path(explicit_path)] if explicit_path else []
    candidates.extend(_default_config_candidates())
    for path in candidates:
        if path and path.exists():
            with path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            if isinstance(payload, dict):
                payload["_source_path"] = str(path)
                return payload
    return {}


def _default_config_candidates() -> list[Path]:
    cwd = Path.cwd().resolve()
    candidates: list[Path] = []
    for parent in [cwd, *cwd.parents]:
        candidates.append(parent / "config" / "llm.local.json")
    return candidates


def _apply_env_overrides(payload: dict[str, Any]) -> dict[str, Any]:
    env_map = {
        "RECRUIT_LLM_ENABLED": "enabled",
        "RECRUIT_LLM_BASE_URL": "base_url",
        "RECRUIT_LLM_API_KEY": "api_key",
        "RECRUIT_LLM_MODEL": "model",
        "RECRUIT_EMBEDDING_BASE_URL": "embedding_base_url",
        "RECRUIT_EMBEDDING_API_KEY": "embedding_api_key",
        "RECRUIT_EMBEDDING_MODEL": "embedding_model",
        "RECRUIT_LLM_TIMEOUT_SECONDS": "timeout_seconds",
        "RECRUIT_LLM_TEMPERATURE": "temperature",
        "RECRUIT_LLM_JSON_MODE": "json_mode",
        "RECRUIT_LLM_TOP_P": "top_p",
        "RECRUIT_LLM_STRICT_JSON_SCHEMA": "strict_json_schema",
        "RECRUIT_LLM_THINKING_MODE": "thinking_mode",
        "RECRUIT_LLM_FAIL_OPEN": "fail_open",
    }
    merged = dict(payload)
    for env_key, payload_key in env_map.items():
        if env_key in os.environ:
            merged[payload_key] = os.environ[env_key]
    return merged


def _deep_merge(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)
