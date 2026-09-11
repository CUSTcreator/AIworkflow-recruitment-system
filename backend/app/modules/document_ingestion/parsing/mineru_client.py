from __future__ import annotations

import io
import json
import time
import zipfile
from dataclasses import dataclass
from hashlib import sha256
from pathlib import PurePosixPath
from typing import Any, Callable

import httpx

from backend.app.infrastructure.workflow_runtime.external_activity import ExternalActivity
from backend.app.infrastructure.workflow_runtime.external_error_policy import (
    ExternalHttpError,
    retry_after_seconds_from_headers,
)
_PENDING_STATES = {"waiting-file", "pending", "running", "converting"}


class MinerUError(RuntimeError):
    pass


@dataclass(slots=True)
class MinerUResult:
    text: str
    blocks: list[dict[str, Any]]
    page_count: int | None
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class MinerUSubmission:
    """已提交给 MinerU 的异步任务，只保存恢复轮询所需的稳定标识。"""

    batch_id: str
    filename: str
    data_id: str | None


@dataclass(frozen=True, slots=True)
class MinerUPollResult:
    """一次轮询的结果；pending 不抛异常，让 StepRunner 延迟当前 Step。"""

    state: str
    batch_id: str
    filename: str
    data_id: str | None
    item: dict[str, Any] | None = None
    trace_id: str | None = None


class MinerUClient:
    """MinerU v4 客户端。

    ``submit → poll_once → download_completed`` 是唯一解析接口。轮询等待和瞬态
    重试必须由 Workflow/StepRunner 管理，客户端不得在 Worker 线程内循环等待。
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model_version: str = "vlm",
        timeout_seconds: int = 300,
        request_timeout_seconds: int = 30,
        enable_formula: bool = False,
        enable_table: bool = True,
        language: str = "ch",
        max_result_bytes: int = 100 * 1024 * 1024,
        transport: httpx.BaseTransport | None = None,
        activity: ExternalActivity | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key.strip()
        self.model_version = model_version
        self.timeout_seconds = max(1, timeout_seconds)
        self.request_timeout_seconds = max(1, request_timeout_seconds)
        self.enable_formula = enable_formula
        self.enable_table = enable_table
        self.language = language
        self.max_result_bytes = max_result_bytes
        self.transport = transport
        self._activity = activity or ExternalActivity()

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    def submit(
        self,
        data: bytes,
        filename: str,
        *,
        data_id: str | None = None,
        is_ocr: bool = False,
        timeout_seconds: int | None = None,
        idempotency_key: str,
    ) -> MinerUSubmission:
        """提交一次解析和文件上传；该操作可安全重试，返回 batch_id 供后续轮询。"""
        self._validate_input(data, filename)
        request_timeout = self._request_timeout(timeout_seconds)
        file_descriptor: dict[str, Any] = {"name": filename, "is_ocr": bool(is_ocr)}
        if data_id:
            file_descriptor["data_id"] = data_id
        request_body = {
            "files": [file_descriptor],
            "model_version": self.model_version,
            "enable_formula": self.enable_formula,
            "enable_table": self.enable_table,
            "language": self.language,
        }
        with self._http_client(request_timeout) as client:
            submit_payload = self._call(
                "submit",
                idempotency_key,
                lambda key: self._json_request(
                    client,
                    "POST",
                    f"{self.base_url}/api/v4/file-urls/batch",
                    headers={**self._headers(), "Idempotency-Key": key},
                    json_body=request_body,
                ),
            )
            submit_data = self._api_data(submit_payload, "mineru_submit_failed")
            batch_id = str(submit_data.get("batch_id") or "")
            upload_urls = submit_data.get("file_urls")
            if not batch_id or not isinstance(upload_urls, list) or len(upload_urls) != 1:
                raise MinerUError("mineru_submit_response_invalid")
            self._call(
                "upload",
                self._child_key(idempotency_key, "upload"),
                lambda _key: self._upload_file(client, str(upload_urls[0]), data),
            )
        return MinerUSubmission(batch_id=batch_id, filename=filename, data_id=data_id)

    def poll_once(
        self,
        submission: MinerUSubmission,
        *,
        timeout_seconds: int | None = None,
        idempotency_key: str,
    ) -> MinerUPollResult:
        """只查询一次 MinerU 状态；pending 由调用方写为 waiting_external。"""
        request_timeout = self._request_timeout(timeout_seconds)
        with self._http_client(request_timeout) as client:
            payload = self._call(
                "poll",
                self._child_key(idempotency_key, f"poll:{submission.batch_id}"),
                lambda key: self._json_request(
                    client,
                    "GET",
                    f"{self.base_url}/api/v4/extract-results/batch/{submission.batch_id}",
                    headers={**self._headers(), "Idempotency-Key": key},
                ),
            )
        response_data = self._api_data(payload, "mineru_poll_failed")
        items = response_data.get("extract_result")
        if not isinstance(items, list) or not items:
            raise MinerUError("mineru_poll_response_invalid")
        item = self._select_result(items, filename=submission.filename, data_id=submission.data_id)
        state = str(item.get("state") or "")
        trace_id = self._optional_string(payload.get("trace_id"))
        if state == "done":
            return MinerUPollResult("done", submission.batch_id, submission.filename, submission.data_id, item, trace_id)
        if state == "failed":
            reason = str(item.get("err_msg") or "unknown")
            raise MinerUError(f"mineru_parse_failed:{reason[:500]}")
        if state not in _PENDING_STATES:
            raise MinerUError(f"mineru_unknown_state:{state or 'missing'}")
        return MinerUPollResult("pending", submission.batch_id, submission.filename, submission.data_id, item, trace_id)

    def download_completed(
        self,
        poll: MinerUPollResult,
        *,
        timeout_seconds: int | None = None,
        idempotency_key: str,
    ) -> MinerUResult:
        """下载已完成任务的结果；下载失败交给当前 Step 的统一重试。"""
        if poll.state != "done" or poll.item is None:
            raise MinerUError("mineru_completed_result_required")
        zip_url = str(poll.item.get("full_zip_url") or "")
        if not zip_url:
            raise MinerUError("mineru_result_missing_zip_url")
        started = time.monotonic()
        request_timeout = self._request_timeout(timeout_seconds)
        with self._http_client(request_timeout) as client:
            response = self._call(
                "download",
                self._child_key(idempotency_key, f"download:{poll.batch_id}"),
                lambda _key: self._download_file(client, zip_url),
            )
        zip_bytes = response.content
        if len(zip_bytes) > self.max_result_bytes:
            raise MinerUError("mineru_result_zip_too_large")
        markdown, content_list, archive_metadata = self._read_result_archive(zip_bytes)
        page_count = self._page_count(poll.item, content_list)
        from .document_blocks import reconcile_mineru_blocks

        blocks, reconciliation = reconcile_mineru_blocks(markdown, content_list)
        return MinerUResult(
            text=markdown,
            blocks=blocks,
            page_count=page_count,
            metadata={
                "provider": "mineru",
                "api_version": "v4",
                "model_version": self.model_version,
                "batch_id": poll.batch_id,
                "trace_id": poll.trace_id,
                "data_id": poll.data_id,
                "duration_ms": round((time.monotonic() - started) * 1000, 2),
                "block_reconciliation": reconciliation,
                **archive_metadata,
            },
        )

    def _validate_input(self, data: bytes, filename: str) -> None:
        if not self.configured:
            raise MinerUError("mineru_not_configured")
        if not data:
            raise MinerUError("mineru_empty_file")
        if len(data) > 200 * 1024 * 1024:
            raise MinerUError("mineru_file_too_large")
        if not filename.lower().endswith(".pdf"):
            raise MinerUError("mineru_pdf_required")

    def _request_timeout(self, timeout_seconds: int | None) -> int:
        return min(self.request_timeout_seconds, max(1, int(timeout_seconds))) if timeout_seconds is not None else self.request_timeout_seconds

    def _http_client(self, timeout: int) -> httpx.Client:
        return httpx.Client(timeout=timeout, transport=self.transport, follow_redirects=True)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _call(
        self,
        operation: str,
        idempotency_key: str,
        invoke: Callable[[str], Any],
    ) -> Any:
        return self._activity.call(
            service="mineru",
            operation=operation,
            idempotency_key=idempotency_key,
            activity_type="mineru",
            activity_key=f"mineru_{operation}",
            diagnostics={
                "remote_status": "submitted" if operation == "submit" else operation
            },
            invoke=invoke,
        )

    @staticmethod
    def _child_key(root: str, suffix: str) -> str:
        return sha256(f"{root}:{suffix}".encode("utf-8")).hexdigest()

    @staticmethod
    def _upload_file(client: httpx.Client, url: str, data: bytes) -> None:
        response = client.put(url, content=data, headers={})
        if not 200 <= response.status_code < 300:
            raise ExternalHttpError(
                f"mineru_upload_failed:http_{response.status_code}",
                status_code=response.status_code,
                retry_after_seconds=retry_after_seconds_from_headers(response.headers),
            )

    @staticmethod
    def _download_file(client: httpx.Client, url: str) -> httpx.Response:
        response = client.get(url)
        if not 200 <= response.status_code < 300:
            raise ExternalHttpError(
                f"mineru_result_download_failed:http_{response.status_code}",
                status_code=response.status_code,
                retry_after_seconds=retry_after_seconds_from_headers(response.headers),
            )
        return response

    @staticmethod
    def _select_result(items: list[Any], *, filename: str, data_id: str | None) -> dict[str, Any]:
        valid = [item for item in items if isinstance(item, dict)]
        if data_id:
            matched = [item for item in valid if str(item.get("data_id") or "") == data_id]
            if matched:
                return matched[0]
        matched = [item for item in valid if str(item.get("file_name") or "") == filename]
        if matched:
            return matched[0]
        if len(valid) == 1:
            return valid[0]
        raise MinerUError("mineru_result_file_not_found")

    @staticmethod
    def _json_request(client: httpx.Client, method: str, url: str, *, headers: dict[str, str], json_body: dict[str, Any] | None = None) -> dict[str, Any]:
        response = client.request(method, url, headers=headers, json=json_body)
        if not 200 <= response.status_code < 300:
            raise ExternalHttpError(
                f"mineru_http_error:{response.status_code}:{response.text[:500]}",
                status_code=response.status_code,
                retry_after_seconds=retry_after_seconds_from_headers(response.headers),
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise MinerUError("mineru_response_not_json") from exc
        if not isinstance(payload, dict):
            raise MinerUError("mineru_response_invalid")
        return payload

    @staticmethod
    def _api_data(payload: dict[str, Any], prefix: str) -> dict[str, Any]:
        if payload.get("code") != 0:
            raise MinerUError(f"{prefix}:{payload.get('code')}:{str(payload.get('msg') or '')[:500]}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise MinerUError(f"{prefix}:missing_data")
        return data

    @staticmethod
    def _read_result_archive(zip_bytes: bytes) -> tuple[str, list[Any] | None, dict[str, Any]]:
        try:
            archive = zipfile.ZipFile(io.BytesIO(zip_bytes))
        except (zipfile.BadZipFile, OSError) as exc:
            raise MinerUError("mineru_result_invalid_zip") from exc
        with archive:
            files = [item for item in archive.infolist() if not item.is_dir()]
            if sum(item.file_size for item in files) > 200 * 1024 * 1024:
                raise MinerUError("mineru_result_uncompressed_too_large")
            markdown_files = [item for item in files if PurePosixPath(item.filename.replace("\\", "/")).name == "full.md"]
            if not markdown_files:
                raise MinerUError("mineru_result_missing_full_markdown")
            markdown_file = min(markdown_files, key=lambda item: len(item.filename))
            markdown = archive.read(markdown_file).decode("utf-8", errors="replace").strip()
            if not markdown:
                raise MinerUError("mineru_result_empty_markdown")
            content_files = [
                item for item in files
                if item.filename.replace("\\", "/").endswith("_content_list.json")
                or PurePosixPath(item.filename.replace("\\", "/")).name == "content_list.json"
            ]
            content_list: list[Any] | None = None
            if content_files:
                try:
                    decoded = json.loads(archive.read(content_files[0]).decode("utf-8"))
                    if isinstance(decoded, list):
                        content_list = decoded
                except (UnicodeDecodeError, json.JSONDecodeError):
                    content_list = None
            return markdown, content_list, {
                "markdown_file": markdown_file.filename,
                "content_list_file": content_files[0].filename if content_files else None,
                "content_block_count": len(content_list) if content_list is not None else None,
            }

    @staticmethod
    def _page_count(result_item: dict[str, Any], content_list: list[Any] | None) -> int | None:
        progress = result_item.get("extract_progress")
        if isinstance(progress, dict):
            total = progress.get("total_pages")
            if isinstance(total, int) and total > 0:
                return total
        if isinstance(content_list, list):
            page_indexes = [item.get("page_idx") for item in content_list if isinstance(item, dict) and isinstance(item.get("page_idx"), int)]
            if page_indexes:
                return max(page_indexes) + 1
        return None

    @staticmethod
    def _optional_string(value: Any) -> str | None:
        return str(value) if value is not None else None
