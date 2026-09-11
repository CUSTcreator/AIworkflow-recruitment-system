from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from typing import Any

import httpx

from backend.app.infrastructure.workflow_runtime.external_activity import ExternalActivity
from backend.app.infrastructure.workflow_runtime.external_error_policy import (
    ExternalHttpError,
    retry_after_seconds_from_headers,
)
from .contracts import ParseCandidate
from .document_blocks import blocks_from_markdown


class VisionLLMError(RuntimeError):
    pass


VISION_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["pages"],
    "properties": {
        "pages": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["page", "markdown"],
                "properties": {
                    "page": {"type": "integer", "minimum": 1},
                    "markdown": {"type": "string", "minLength": 1},
                },
            },
        },
    },
}


class VisionLLMProvider:
    """OpenAI 兼容的视觉转录适配器。

    渲染 PDF 是本地计算；单次 HTTP 请求通过 ``ExternalActivity`` 统一记录稳定
    幂等键和失败分类。工作流重试由 StepRunner 负责，本类不实现隐藏重试循环。
    """

    def __init__(
        self,
        *,
        enabled: bool = False,
        base_url: str = "",
        api_key: str = "",
        model: str = "",
        timeout_seconds: int = 180,
        dpi: int = 144,
        max_pages: int = 20,
        enable_thinking: bool = False,
        response_format: str = "json_object",
        transport: httpx.BaseTransport | None = None,
        activity: ExternalActivity | None = None,
    ) -> None:
        self.enabled = enabled
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key.strip()
        self.model = model.strip()
        self.timeout_seconds = max(1, timeout_seconds)
        self.dpi = min(220, max(96, dpi))
        self.max_pages = max(1, max_pages)
        self.enable_thinking = enable_thinking
        self.response_format = response_format.strip().lower()
        self.transport = transport
        self._activity = activity or ExternalActivity()

    @property
    def configured(self) -> bool:
        return bool(self.enabled and self.base_url and self.api_key and self.model)

    def parse(
        self,
        data: bytes,
        filename: str,
        *,
        timeout_seconds: int | None = None,
        idempotency_key: str | None = None,
        page_numbers: list[int] | None = None,
    ) -> ParseCandidate:
        if not self.configured:
            raise VisionLLMError("vision_llm_not_configured")
        images, document_page_count = self._render_pages(data, page_numbers)
        if not images:
            raise VisionLLMError("vision_llm_pdf_has_no_pages")
        requested_pages = [page for page, _image in images]
        started = time.monotonic()
        request_timeout = min(self.timeout_seconds, max(1, int(timeout_seconds))) if timeout_seconds is not None else self.timeout_seconds
        content: list[dict[str, Any]] = [{
            "type": "text",
            "text": (
                "请按页面顺序忠实转录这份文档。禁止总结、补写、纠错或省略；"
                "保留标题、列表、表格文字、数字、日期、百分比、邮箱、手机号和技术名词。"
                f"本次只转录原文页码{requested_pages}，page必须使用原始PDF页码。"
                "只返回Schema要求的pages、page、markdown字段，不要返回解释、置信度或其他字段。"
                "返回JSON必须符合以下Schema："
                + json.dumps(VISION_RESPONSE_SCHEMA, ensure_ascii=False, separators=(",", ":"))
            ),
        }]
        for page, image_bytes in images:
            content.append({"type": "text", "text": f"第{page}页："})
            content.append({"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(image_bytes).decode("ascii")}})
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "你是文档转录器，只能根据页面图像逐字转录。不得推断图像中不存在的信息。"},
                {"role": "user", "content": content},
            ],
            "temperature": 0,
            "enable_thinking": self.enable_thinking,
            "response_format": self._response_format_payload(),
        }
        request_key = idempotency_key or hashlib.sha256(
            f"vision:{filename}:{requested_pages}:{hashlib.sha256(data).hexdigest()}".encode("utf-8")
        ).hexdigest()
        with httpx.Client(timeout=request_timeout, transport=self.transport, follow_redirects=True) as client:
            response = self._activity.call(
                service="vision_llm",
                operation="transcribe_document",
                idempotency_key=request_key,
                activity_type="llm",
                activity_key="vision_llm_transcribe",
                diagnostics={"model": self.model},
                invoke=lambda key: self._post(client, body, key),
            )
        payload = self._response_payload(response)
        pages, response_warnings = self._validated_pages(payload, requested_pages)
        page_markdown: list[str] = []
        blocks: list[dict[str, Any]] = []
        for item in pages:
            expected_page = int(item["page"])
            markdown = str(item["markdown"]).strip()
            page_markdown.append(markdown)
            for block in blocks_from_markdown(markdown, source_parser="vision_llm"):
                block["block_id"] = f"B_{len(blocks) + 1:04d}"
                block["page"] = expected_page
                block["order"] = len(blocks) + 1
                blocks.append(block)
        return ParseCandidate(
            provider="vision_llm",
            text="\n\n".join(page_markdown),
            blocks=blocks,
            page_count=document_page_count,
            metadata={
                "provider": "vision_llm",
                "model": self.model,
                "filename": filename,
                "duration_ms": round((time.monotonic() - started) * 1000, 2),
                "usage": response.json().get("usage", {}),
                "requested_pages": requested_pages,
                "returned_pages": [int(item["page"]) for item in pages],
                "missing_pages": [
                    page for page in requested_pages
                    if page not in {int(item["page"]) for item in pages}
                ],
                "response_warnings": response_warnings,
            },
        )

    def _post(self, client: httpx.Client, body: dict[str, Any], idempotency_key: str) -> httpx.Response:
        # 保留 httpx 原始网络异常给 ExternalActivity 分类；不能预先包装成
        # VisionLLMError，否则 TLS EOF / 协议断连会被误判为永久业务错误。
        response = client.post(
            self.base_url + "/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key,
            },
            json=body,
        )
        if not 200 <= response.status_code < 300:
            raise ExternalHttpError(
                f"vision_llm_http_error:{response.status_code}:{response.text[:300]}",
                status_code=response.status_code,
                retry_after_seconds=retry_after_seconds_from_headers(response.headers),
            )
        return response

    def _render_pages(
        self,
        data: bytes,
        page_numbers: list[int] | None,
    ) -> tuple[list[tuple[int, bytes]], int]:
        try:
            import fitz
        except ImportError as exc:
            raise VisionLLMError("vision_llm_pdf_renderer_not_installed") from exc
        try:
            document = fitz.open(stream=data, filetype="pdf")
        except Exception as exc:
            raise VisionLLMError("vision_llm_invalid_pdf") from exc
        try:
            requested = (
                list(range(1, document.page_count + 1))
                if page_numbers is None
                else sorted(set(page_numbers))
            )
            if not requested or any(page < 1 or page > document.page_count for page in requested):
                raise VisionLLMError("vision_llm_page_selection_invalid")
            if len(requested) > self.max_pages:
                raise VisionLLMError("vision_llm_page_limit_exceeded")
            return (
                [
                    (
                        page_number,
                        document[page_number - 1]
                        .get_pixmap(dpi=self.dpi, alpha=False)
                        .tobytes("jpeg"),
                    )
                    for page_number in requested
                ],
                document.page_count,
            )
        finally:
            document.close()

    def _response_format_payload(self) -> dict[str, Any]:
        if self.response_format == "json_schema":
            return {
                "type": "json_schema",
                "json_schema": {
                    "name": "document_page_transcription",
                    "strict": True,
                    "schema": VISION_RESPONSE_SCHEMA,
                },
            }
        return {"type": "json_object"}

    @staticmethod
    def _response_payload(response: httpx.Response) -> dict[str, Any]:
        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise VisionLLMError("vision_llm_response_invalid") from exc
        if isinstance(content, list):
            content = "".join(str(item.get("text") or "") for item in content if isinstance(item, dict))
        text = str(content).strip()
        fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
        if fenced:
            text = fenced.group(1)
        elif not text.startswith("{"):
            start, end = text.find("{"), text.rfind("}")
            if start >= 0 and end > start:
                text = text[start : end + 1]
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise VisionLLMError("vision_llm_response_not_json") from exc
        if not isinstance(payload, dict):
            raise VisionLLMError("vision_llm_response_not_object")
        return payload

    @staticmethod
    def _validated_pages(
        payload: dict[str, Any],
        requested_pages: list[int],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Accept pages independently so one malformed item cannot erase all.

        The request remains strict and minimal. Runtime validation is deliberately
        tolerant: unexpected fields are discarded, while missing or malformed
        pages are reported for source-preserving fallback by the caller.
        """

        raw_pages = payload.get("pages")
        if not isinstance(raw_pages, list):
            raise VisionLLMError("vision_llm_pages_not_array")
        requested = set(requested_pages)
        accepted: dict[int, dict[str, Any]] = {}
        warnings: list[str] = []
        if set(payload) - {"pages"}:
            warnings.append("vision_response_extra_top_level_fields_discarded")
        for item in raw_pages:
            if not isinstance(item, dict):
                warnings.append("vision_response_non_object_page_discarded")
                continue
            page = item.get("page")
            markdown = item.get("markdown")
            if isinstance(page, bool) or not isinstance(page, int) or page not in requested:
                warnings.append("vision_response_unrequested_page_discarded")
                continue
            if not isinstance(markdown, str) or not markdown.strip():
                warnings.append(f"vision_response_empty_page_discarded:{page}")
                continue
            if page in accepted:
                warnings.append(f"vision_response_duplicate_page_discarded:{page}")
                continue
            if set(item) - {"page", "markdown"}:
                warnings.append(f"vision_response_extra_page_fields_discarded:{page}")
            accepted[page] = {"page": page, "markdown": markdown.strip()}
        if not accepted:
            raise VisionLLMError("vision_llm_no_valid_pages")
        missing = [page for page in requested_pages if page not in accepted]
        warnings.extend(f"vision_response_page_missing:{page}" for page in missing)
        return [accepted[page] for page in requested_pages if page in accepted], warnings
