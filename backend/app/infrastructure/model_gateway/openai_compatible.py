from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import urllib.error
import urllib.request
import uuid
from typing import Any

from backend.app.infrastructure.workflow_runtime.external_activity import ExternalActivity
from backend.app.infrastructure.workflow_runtime.external_error_policy import retry_after_seconds_from_headers
from recruitment_ai_core.execution import (
    GLOBAL_LLM_LIMITER,
    remaining_execution_timeout_seconds,
)
from recruitment_ai_core.execution.model_call_context import ModelCallContext
from recruitment_ai_core.llm.config import LLMSettings, load_llm_settings
from recruitment_ai_core.llm.errors import LLMCallError, LLMResponseError
from recruitment_ai_core.llm.schema_validator import (
    JSONSchemaValidationError,
    validate_json_schema,
)


logger = logging.getLogger("recruitment_ai_core.llm")


def call_json_llm(
    *,
    workflow_name: str,
    messages: list[dict[str, str]],
    schema_name: str,
    settings_overrides: dict[str, Any] | None = None,
    json_schema: dict[str, Any] | None = None,
    local_validation_schema: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    settings = load_llm_settings(settings_overrides)
    trace = settings.trace(workflow_name)
    trace["schema_name"] = schema_name
    if not settings.is_enabled_for(workflow_name):
        trace["timeout_seconds"] = settings.timeout_for(workflow_name)
        trace["mode"] = "disabled"
        return None, trace
    timeout_seconds = _bounded_timeout(settings.timeout_for(workflow_name))
    trace["timeout_seconds"] = timeout_seconds
    _validate_settings(settings, workflow_name)
    trace["mode"] = "openai_compatible"
    call_id = uuid.uuid4().hex[:12]
    started = time.perf_counter()
    trace.update({"call_id": call_id, "started_at_epoch_ms": int(time.time() * 1000)})
    logger.info("llm_call_started call_id=%s workflow=%s schema=%s model=%s", call_id, workflow_name, schema_name, settings.model_for(workflow_name))
    try:
        global_limit = max(
            1, int(settings.execution.get("global_llm_max_in_flight", 12))
        )
        with GLOBAL_LLM_LIMITER.slot(global_limit) as limiter_wait_ms:
            trace["limiter_wait_ms"] = limiter_wait_ms
            trace["global_llm_max_in_flight"] = global_limit
            content, response_meta = _chat_completion(
                settings,
                workflow_name,
                messages,
                schema_name=schema_name,
                json_schema=json_schema,
                timeout_seconds=timeout_seconds,
                idempotency_key=idempotency_key,
            )
        trace.update(response_meta)
        parsed = _parse_json_content(content)
        validation_schema = (
            local_validation_schema
            if local_validation_schema is not None
            else json_schema
        )
        if validation_schema is not None:
            try:
                validate_json_schema(parsed, validation_schema)
            except JSONSchemaValidationError as exc:
                raise LLMResponseError(f"llm_response_schema_invalid:{exc}") from exc
    except Exception as exc:
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        trace.update(
            {
                "duration_ms": duration_ms,
                "completed_at_epoch_ms": int(time.time() * 1000),
                "status": "failed",
                "error": f"{type(exc).__name__}:{str(exc)[:240]}",
            }
        )
        try:
            setattr(exc, "llm_trace", dict(trace))
        except Exception:
            pass
        logger.warning(
            "llm_call_failed call_id=%s workflow=%s schema=%s duration_ms=%s error=%s",
            call_id, workflow_name, schema_name, duration_ms, f"{type(exc).__name__}:{str(exc)[:240]}",
        )
        raise
    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    trace.update({"duration_ms": duration_ms, "completed_at_epoch_ms": int(time.time() * 1000), "status": "success"})
    logger.info("llm_call_completed call_id=%s workflow=%s schema=%s duration_ms=%s", call_id, workflow_name, schema_name, duration_ms)
    return parsed, trace


def call_embeddings(
    *,
    texts: list[str],
    settings_overrides: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> tuple[list[list[float]], dict[str, Any]]:
    settings = load_llm_settings(settings_overrides)
    trace = settings.embedding_trace()
    if not settings.enabled:
        raise LLMCallError("embedding_disabled")
    if not settings.embedding_model:
        raise LLMCallError("missing_embedding_config:embedding_model")
    base_url = settings.embedding_base_url or settings.base_url
    api_key = settings.embedding_api_key or settings.api_key
    if not base_url or not api_key:
        raise LLMCallError("missing_embedding_config:base_url_or_api_key")
    vectors: list[list[float]] = []
    request_count = 0
    total_started = time.perf_counter()
    for start in range(0, len(texts), settings.embedding_batch_size):
        batch = texts[start : start + settings.embedding_batch_size]
        call_id = uuid.uuid4().hex[:12]
        batch_started = time.perf_counter()
        logger.info(
            "embedding_call_started call_id=%s model=%s batch_size=%s",
            call_id,
            settings.embedding_model,
            len(batch),
        )
        request = urllib.request.Request(
            base_url.rstrip("/") + "/embeddings",
            data=json.dumps({"model": settings.embedding_model, "input": batch}, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", **({"Idempotency-Key": idempotency_key} if idempotency_key else {})},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=_bounded_timeout(settings.timeout_seconds)) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            logger.warning(
                "embedding_call_failed call_id=%s model=%s duration_ms=%s error=http_%s",
                call_id,
                settings.embedding_model,
                round((time.perf_counter() - batch_started) * 1000, 2),
                exc.code,
            )
            error = LLMCallError(f"embedding_http_error:{exc.code}:{detail[:500]}")
            # LLMCallError 保留供应商响应元数据，ExternalActivity 才能正确处理
            # 限流和服务端临时失败，而不是依赖文案猜测。
            setattr(error, "status_code", exc.code)
            setattr(error, "retry_after_seconds", retry_after_seconds_from_headers(exc.headers))
            raise error from exc
        except Exception as exc:
            logger.warning(
                "embedding_call_failed call_id=%s model=%s duration_ms=%s error=%s",
                call_id,
                settings.embedding_model,
                round((time.perf_counter() - batch_started) * 1000, 2),
                f"{type(exc).__name__}:{str(exc)[:240]}",
            )
            raise LLMCallError(f"embedding_call_failed:{exc}") from exc
        try:
            ordered = sorted(payload["data"], key=lambda item: item["index"])
            vectors.extend([[float(value) for value in item["embedding"]] for item in ordered])
        except (KeyError, TypeError, ValueError) as exc:
            raise LLMResponseError("embedding_response_invalid") from exc
        request_count += 1
        logger.info(
            "embedding_call_completed call_id=%s model=%s batch_size=%s duration_ms=%s",
            call_id,
            settings.embedding_model,
            len(batch),
            round((time.perf_counter() - batch_started) * 1000, 2),
        )
    if len(vectors) != len(texts) or any(not vector for vector in vectors):
        raise LLMResponseError("embedding_response_count_mismatch")
    trace["mode"] = "openai_compatible_embeddings"
    trace["input_count"] = len(texts)
    trace["request_count"] = request_count
    trace["duration_ms"] = round((time.perf_counter() - total_started) * 1000, 2)
    return vectors, trace


def _direct_request_key(
    kind: str,
    workflow_name: str,
    operation: str,
    payload: Any,
    schema: dict[str, Any] | None,
) -> str:
    """为非 Workflow 调用提供稳定关联 ID；不赋予其 Step 级自动重试语义。"""
    raw = json.dumps(
        {"kind": kind, "workflow": workflow_name, "operation": operation, "payload": payload, "schema": schema},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
def _bounded_timeout(configured_seconds: int) -> int:
    """取配置超时与当前 Workflow Step 剩余预算的较小值。"""
    remaining = remaining_execution_timeout_seconds()
    if remaining is None:
        return max(1, configured_seconds)
    if remaining <= 0:
        raise TimeoutError("workflow_step_timeout_budget_exhausted")
    return max(1, min(configured_seconds, remaining))


def _validate_settings(settings: LLMSettings, workflow_name: str) -> None:
    if settings.provider != "openai_compatible":
        raise LLMCallError(f"unsupported_llm_provider:{settings.provider}")
    missing = []
    if not settings.base_url:
        missing.append("base_url")
    if not settings.api_key:
        missing.append("api_key")
    if not settings.model_for(workflow_name):
        missing.append("model")
    if missing:
        raise LLMCallError(f"missing_llm_config:{','.join(missing)}")


def _chat_completion(
    settings: LLMSettings,
    workflow_name: str,
    messages: list[dict[str, str]],
    *,
    schema_name: str,
    json_schema: dict[str, Any] | None,
    timeout_seconds: int | None = None,
    idempotency_key: str | None = None,
) -> tuple[str, dict[str, Any]]:
    url = settings.base_url.rstrip("/") + "/chat/completions"
    strict_schema = bool(json_schema and settings.strict_json_schema_for(workflow_name))
    json_mode = settings.json_mode_for(workflow_name)
    request_messages = [dict(message) for message in messages]
    if json_mode and not strict_schema:
        instruction = "只输出一个 JSON 对象，不得输出 Markdown 或解释。"
        if json_schema:
            instruction += (
                "JSON 必须包含 Schema 的全部 required 字段；没有结果时使用空数组或 null，禁止省略字段。"
                f"\nJSON Schema:\n{json.dumps(json_schema, ensure_ascii=False)}"
            )
        request_messages.append({"role": "system", "content": instruction})
    body: dict[str, Any] = {
        "model": settings.model_for(workflow_name),
        "messages": request_messages,
    }
    thinking_mode = settings.thinking_mode_for(workflow_name)
    if thinking_mode is not None:
        body["thinking"] = {"type": thinking_mode}
    if thinking_mode != "enabled":
        body["temperature"] = settings.temperature_for(workflow_name)
        body["top_p"] = settings.top_p_for(workflow_name)
    if strict_schema:
        body["response_format"] = {"type": "json_schema", "json_schema": {"name": schema_name, "strict": True, "schema": json_schema}}
    elif json_mode:
        body["response_format"] = {"type": "json_object"}
    request = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {settings.api_key}", "Content-Type": "application/json", **({"Idempotency-Key": idempotency_key} if idempotency_key else {})},
        method="POST",
    )
    timeout_seconds = _bounded_timeout(timeout_seconds or settings.timeout_for(workflow_name))
    deadline = time.monotonic() + timeout_seconds
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(
                _read_response_before_deadline(response, deadline).decode("utf-8")
            )
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        error = LLMCallError(f"llm_http_error:{exc.code}:{detail[:500]}")
        setattr(error, "status_code", exc.code)
        setattr(error, "retry_after_seconds", retry_after_seconds_from_headers(exc.headers))
        raise error from exc
    except Exception as exc:  # Network providers fail in many provider-specific ways.
        raise LLMCallError(f"llm_call_failed:{exc}") from exc
    try:
        return str(payload["choices"][0]["message"]["content"]), {
            "response_model": payload.get("model"),
            "usage": payload.get("usage", {}),
        }
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMResponseError("llm_response_missing_message_content") from exc


def _read_response_before_deadline(response: Any, deadline: float) -> bytes:
    read_chunk = getattr(response, "read1", None)
    if read_chunk is None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("llm_total_deadline_exceeded")
        _set_response_socket_timeout(response, remaining)
        payload = response.read()
        if time.monotonic() > deadline:
            raise TimeoutError("llm_total_deadline_exceeded")
        return payload
    chunks: list[bytes] = []
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("llm_total_deadline_exceeded")
        _set_response_socket_timeout(response, remaining)
        chunk = read_chunk(64 * 1024)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _set_response_socket_timeout(response: Any, remaining: float) -> None:
    try:
        response.fp.raw._sock.settimeout(max(0.001, remaining))
    except (AttributeError, OSError):
        pass


def _parse_json_content(content: str) -> dict[str, Any]:
    text = content.strip()
    if not text:
        raise LLMResponseError("llm_response_empty")
    parsed = _decode_first_json_value(text)
    if not isinstance(parsed, dict):
        raise LLMResponseError("llm_response_json_not_object")
    return parsed


def _decode_first_json_value(text: str) -> Any:
    """Decode the first complete JSON value and ignore provider-added suffixes.

    Some OpenAI-compatible providers occasionally append an explanation or a
    duplicate JSON object despite ``json_object`` mode. The first object still
    has to pass the caller's schema and business validation, so retaining it is
    safer than degrading an otherwise usable whole batch.
    """

    decoder = json.JSONDecoder()
    candidates = [text]
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    if fenced:
        candidates.insert(0, fenced.group(1).strip())
    for candidate in candidates:
        starts = [0, *(match.start() for match in re.finditer(r"[\[{]", candidate))]
        for start in dict.fromkeys(starts):
            try:
                parsed, _ = decoder.raw_decode(candidate[start:].lstrip())
            except json.JSONDecodeError:
                continue
            return parsed
    raise LLMResponseError("llm_response_not_json")


class OpenAICompatibleModelGateway:
    """OpenAI 兼容模型的后端适配器；工作流内调用统一经过 ExternalActivity。"""

    def __init__(self, activity: ExternalActivity | None = None) -> None:
        self.activity = activity or ExternalActivity()

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
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        # 即使是同步管理操作，也要经过同一外部调用边界；只是没有 StepRunner 时
        # 不会自动重试。工作流内仍使用由 StepContext 派生的稳定请求 ID。
        request_id = (
            request_context.request_id
            if request_context is not None
            else _direct_request_key("json", workflow_name, schema_name, messages, json_schema)
        )
        operation = request_context.operation if request_context is not None else f"{workflow_name}:{schema_name}"
        return self.activity.call(
            service="llm",
            operation=operation,
            idempotency_key=request_id,
            invoke=lambda activity_request_id: call_json_llm(
                workflow_name=workflow_name,
                messages=messages,
                schema_name=schema_name,
                json_schema=json_schema,
                local_validation_schema=local_validation_schema,
                settings_overrides=settings_overrides,
                idempotency_key=activity_request_id,
            ),
        )

    def embed(
        self,
        texts: list[str],
        *,
        settings_overrides: dict[str, Any] | None = None,
        request_context: ModelCallContext | None = None,
    ) -> tuple[list[list[float]], dict[str, Any]]:
        request_id = (
            request_context.request_id
            if request_context is not None
            else _direct_request_key("embedding", "embeddings", "embed", texts, None)
        )
        operation = request_context.operation if request_context is not None else "embeddings:embed"
        return self.activity.call(
            service="llm",
            operation=operation,
            idempotency_key=request_id,
            invoke=lambda activity_request_id: call_embeddings(
                texts=texts,
                settings_overrides=settings_overrides,
                idempotency_key=activity_request_id,
            ),
        )
