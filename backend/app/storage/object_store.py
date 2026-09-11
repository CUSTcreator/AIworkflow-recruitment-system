from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from io import BytesIO
from pathlib import Path
from typing import Any, TypeVar

from recruitment_ai_core.execution import remaining_execution_timeout_seconds

from backend.app.core.config import settings
from backend.app.infrastructure.observability.logging import log_event
from backend.app.infrastructure.workflow_runtime.external_activity import ExternalActivity
from backend.app.shared.errors import ExternalServiceError

logger = logging.getLogger(__name__)
T = TypeVar("T")


class ObjectStore:
    """对象存储适配器。

    本地存储仅用于开发或 MinIO 降级；所有 MinIO 请求统一经过
    :class:`ExternalActivity`，使工作流能够按当前 Step 重试而非重跑整条流程。
    """

    def __init__(self, *, activity: ExternalActivity | None = None) -> None:
        self._client = None
        self._fallback_dir = Path(settings.local_object_store_dir)
        self._activity = activity or ExternalActivity()

    def put_json(self, object_key: str, payload: dict[str, Any]) -> tuple[str, str]:
        data = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return self.put_bytes(object_key, data, "application/json")

    def read_json(self, object_ref: str, cache_key: str) -> dict[str, Any]:
        path = self.materialize(object_ref, cache_key)
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("stored_json_object_required")
        return payload

    def put_bytes(
        self,
        object_key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> tuple[str, str]:
        sha256 = hashlib.sha256(data).hexdigest()
        if settings.storage_backend == "local":
            return self._put_local(object_key, data, sha256)
        if settings.storage_backend != "minio":
            raise ValueError(f"不支持的对象存储类型：{settings.storage_backend}")
        try:
            self._ensure_bucket()
            self._minio_call(
                "put_object",
                f"{settings.minio_bucket}:{object_key}:{sha256}",
                lambda: self._client.put_object(
                    settings.minio_bucket,
                    object_key,
                    BytesIO(data),
                    length=len(data),
                    content_type=content_type,
                ),
            )
            return f"minio://{settings.minio_bucket}/{object_key}", sha256
        except ExternalServiceError as exc:
            log_event(
                logger,
                logging.WARNING,
                "object_store_put_failed",
                object_key=object_key,
                backend=settings.storage_backend,
                fallback_enabled=settings.allow_storage_fallback,
                error_type=type(exc).__name__,
                error_code=exc.code,
            )
            if not settings.allow_storage_fallback:
                raise
            return self._put_local(object_key, data, sha256)

    def _put_local(self, object_key: str, data: bytes, sha256: str) -> tuple[str, str]:
        target = self._fallback_dir / object_key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return str(target), sha256

    def materialize(self, object_ref: str, cache_key: str) -> Path:
        if not object_ref.startswith("minio://"):
            return Path(object_ref)
        bucket, object_key = self._parse_minio_ref(object_ref)
        response = self._minio_call(
            "get_object",
            f"{bucket}:{object_key}",
            lambda: self._client_or_create().get_object(bucket, object_key),
        )
        try:
            data = response.read()
        except Exception as exc:
            raise self._external_error("read_object_body", exc) from exc
        finally:
            response.close()
            response.release_conn()
        target = self._fallback_dir / "cache" / cache_key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target

    def delete_object(self, object_ref: str) -> None:
        if not object_ref:
            return
        if object_ref.startswith("minio://"):
            bucket, object_key = self._parse_minio_ref(object_ref)
            self._minio_call(
                "remove_object",
                f"{bucket}:{object_key}",
                lambda: self._client_or_create().remove_object(bucket, object_key),
            )
            return

        target = Path(object_ref).resolve()
        storage_root = self._fallback_dir.resolve()
        if target != storage_root and storage_root not in target.parents:
            raise ValueError("拒绝删除对象存储目录之外的文件")
        if target.is_file():
            target.unlink()

    def _ensure_bucket(self) -> None:
        client = self._client_or_create()
        exists = self._minio_call(
            "bucket_exists",
            settings.minio_bucket,
            lambda: client.bucket_exists(settings.minio_bucket),
        )
        if not exists:
            self._minio_call(
                "make_bucket",
                settings.minio_bucket,
                lambda: client.make_bucket(settings.minio_bucket),
            )

    def _client_or_create(self):
        timeout_seconds = self._request_timeout_seconds()
        if self._client is None or self._client_timeout_seconds != timeout_seconds:
            try:
                from minio import Minio
                import urllib3

                # MinIO SDK 通过 urllib3 执行网络 I/O；把当前 Step 剩余预算注入
                # connect/read 超时，防止对象存储调用无界占住 Worker。
                http_client = urllib3.PoolManager(
                    timeout=urllib3.Timeout(connect=timeout_seconds, read=timeout_seconds),
                    retries=False,
                )
                self._client = Minio(
                    settings.minio_endpoint,
                    access_key=settings.minio_access_key,
                    secret_key=settings.minio_secret_key,
                    secure=False,
                    http_client=http_client,
                )
                self._client_timeout_seconds = timeout_seconds
            except Exception as exc:
                raise self._external_error("create_client", exc) from exc
        return self._client

    @staticmethod
    def _request_timeout_seconds() -> int:
        """取 MinIO 配置上限与当前 Step 剩余预算的较小值。"""
        remaining = remaining_execution_timeout_seconds()
        configured = max(1, settings.minio_request_timeout_seconds)
        if remaining is None:
            return configured
        if remaining <= 0:
            raise TimeoutError("workflow_step_timeout_budget_exhausted")
        return max(1, min(configured, remaining))

    def _minio_call(self, operation: str, identity: str, invoke: Callable[[], T]) -> T:
        request_id = hashlib.sha256(
            f"object-store:{operation}:{identity}".encode("utf-8")
        ).hexdigest()
        return self._activity.call(
            service="minio",
            operation=operation,
            idempotency_key=request_id,
            activity_type="minio",
            activity_key=f"minio_{operation}",
            diagnostics={"object_key": identity},
            invoke=lambda _key: invoke(),
        )

    @staticmethod
    def _parse_minio_ref(object_ref: str) -> tuple[str, str]:
        bucket_and_key = object_ref[len("minio://"):]
        bucket, separator, object_key = bucket_and_key.partition("/")
        if not separator or not bucket or not object_key:
            raise ValueError("无效的 MinIO 对象引用")
        return bucket, object_key

    def _external_error(self, operation: str, exc: Exception) -> ExternalServiceError:
        # 客户端初始化或 response.read 也属于 MinIO 外部活动，统一交给 StepRunner 分类。
        return self._activity.call(
            service="minio",
            operation=operation,
            idempotency_key=hashlib.sha256(f"object-store:{operation}".encode("utf-8")).hexdigest(),
            invoke=lambda _key: (_ for _ in ()).throw(exc),
        )