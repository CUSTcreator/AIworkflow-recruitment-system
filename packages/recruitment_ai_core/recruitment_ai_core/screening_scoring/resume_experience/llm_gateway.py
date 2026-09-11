from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Callable

from recruitment_ai_core.llm import call_json_llm, load_llm_settings

from .preset_models import MODEL_CATALOG_VERSION, MODEL_PROMPT_VERSION

CATALOG_VERSION = MODEL_CATALOG_VERSION
PROMPT_VERSION = MODEL_PROMPT_VERSION
SCHEMA_VERSION = "preset_experience_schema_v2_1"


Validator = Callable[[dict[str, Any]], tuple[bool, str]]
WORKFLOW_NAME = "resume_experience_assessment"


class ResumeExperienceLLMGateway:
    def __init__(self, llm_config: dict[str, Any] | None = None):
        self.config = llm_config or {}
        self.settings = load_llm_settings(llm_config)
        options = self.settings.resume_experience
        self.call_config = _workflow_overrides(self.config)
        global_limit = max(
            1, int(self.settings.execution.get("global_llm_max_in_flight", 12))
        )
        self.max_parallel_batches = max(
            1, int(options.get("max_parallel_batches", global_limit))
        )
        self.indicator_batch_size = max(
            1, int(options.get("indicator_batch_size", 8))
        )
        self.traces: list[dict[str, Any]] = []
        self.errors: list[str] = []

    @property
    def enabled(self) -> bool:
        return self.settings.is_enabled_for(WORKFLOW_NAME)

    def call(self, stage: str, payload: dict[str, Any], validator: Validator | None = None) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        validation_error = ""
        for attempt in range(2):
            schema = _schema_for_request(stage, payload)
            body = {"prompt_version": PROMPT_VERSION, "schema_version": SCHEMA_VERSION, "catalog_version": CATALOG_VERSION, "expected_output_schema": schema, **payload}
            if validation_error:
                body["previous_validation_error"] = validation_error
            try:
                parsed, trace = call_json_llm(
                    workflow_name=WORKFLOW_NAME,
                    messages=[{"role": "system", "content": "只返回一个符合 expected_output_schema 的 JSON object，不得输出 JSON 外文本。\n" + _prompt(stage)}, {"role": "user", "content": json.dumps(body, ensure_ascii=False)}],
                    schema_name=f"resume_experience_{stage}_v4",
                    settings_overrides=self.call_config,
                    json_schema=schema,
                    # 完整 Schema 用于约束模型输出；本地只验证可恢复的外层容器，
                    # 让业务归一化器能逐条保留合法结果，而不是一条坏数据拖垮整批。
                    local_validation_schema=_transport_schema(stage),
                )
                self.traces.append({**trace, "stage": stage, "attempt": attempt + 1, "prompt_version": PROMPT_VERSION, "schema_version": SCHEMA_VERSION, "catalog_version": CATALOG_VERSION})
            except Exception as exc:
                if hasattr(exc, "retryable"):
                    raise
                trace = getattr(exc, "llm_trace", {})
                self.traces.append({**trace, "stage": stage, "attempt": attempt + 1, "prompt_version": PROMPT_VERSION, "schema_version": SCHEMA_VERSION, "catalog_version": CATALOG_VERSION})
                validation_error = f"call_failed:{type(exc).__name__}:{str(exc)[:200]}"
                continue
            if parsed is None:
                return None
            if validator:
                valid, validation_error = validator(parsed)
                if not valid:
                    continue
            return parsed
        self.errors.append(f"{stage}:{validation_error}")
        return None

    def audit(self, degraded: bool) -> dict[str, Any]:
        return {"llm_used": bool(self.traces), "prompt_version": PROMPT_VERSION, "schema_version": SCHEMA_VERSION, "catalog_version": CATALOG_VERSION, "degraded": degraded, "errors": self.errors, "traces": self.traces}


def _prompt(stage: str) -> str:
    return (_base_dir() / "prompts" / f"{stage}.md").read_text(encoding="utf-8")


def _schema(stage: str) -> dict[str, Any]:
    return json.loads((_base_dir() / "schemas" / f"{stage}.json").read_text(encoding="utf-8"))


def _schema_for_request(stage: str, payload: dict[str, Any]) -> dict[str, Any]:
    """将本次冻结目录写入模型合同，避免静态 Schema 放行未知 ID。"""
    schema = copy.deepcopy(_schema(stage))
    indicator_ids = sorted(
        str(item.get("indicator_id"))
        for item in payload.get("indicators", [])
        if isinstance(item, dict) and item.get("indicator_id")
    )
    if stage == "work_unit_indicator":
        evaluations = schema["properties"]["evaluations"]["items"]["properties"]
        target_ids = sorted(str(value) for value in payload.get("allowed_target_ids", []) if value)
        if target_ids:
            evaluations["target_id"]["enum"] = target_ids
        if indicator_ids:
            evaluations["activated_indicators"]["items"]["properties"]["indicator_id"]["enum"] = indicator_ids
    elif stage == "project_indicator":
        project_id = payload.get("project_id")
        if project_id:
            schema["properties"]["project_id"]["const"] = str(project_id)
        if indicator_ids:
            schema["properties"]["activated_indicators"]["items"]["$ref"] = "#/$defs/indicatorResult"
            schema["$defs"]["indicatorResult"]["properties"]["indicator_id"]["enum"] = indicator_ids
    return schema


def _transport_schema(stage: str) -> dict[str, Any]:
    if stage == "work_unit_indicator":
        return {
            "type": "object",
            "required": ["evaluations"],
            "properties": {"evaluations": {"type": "array"}},
        }
    if stage == "project_indicator":
        return {
            "type": "object",
            "required": ["project_id", "project_pao", "activated_indicators"],
            "properties": {
                "project_id": {"type": "string"},
                "project_pao": {"type": "object"},
                "activated_indicators": {"type": "array"},
            },
        }
    return _schema(stage)


def _base_dir() -> Path:
    return Path(__file__).resolve().parent


def _workflow_overrides(config: dict[str, Any]) -> dict[str, Any]:
    merged = dict(config)
    workflows = {name: dict(value) for name, value in (config.get("workflows") or {}).items() if isinstance(value, dict)}
    workflow = dict(workflows.get(WORKFLOW_NAME, {}))
    # response_format=json_schema 会让任一坏条目拒绝整个响应，无法实施逐条降级。
    # 完整 Schema 仍随 Prompt 发送；传输层只保证 JSON object，由本地业务层逐条验收。
    workflow.update({"strict_json_schema": False, "json_mode": True})
    workflows[WORKFLOW_NAME] = workflow
    merged["workflows"] = workflows
    return merged
