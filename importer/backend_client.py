from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import httpx

from importer.event_log import log_event


class BackendRequestError(RuntimeError):
    pass


class RecruitmentBackendClient:
    def __init__(
        self,
        base_url: str,
        timeout_seconds: float,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = os.getenv("IMPORTER_USERNAME", "hr").strip()
        self.password = os.getenv("IMPORTER_PASSWORD", "")
        if not self.username or not self.password:
            raise RuntimeError("importer_backend_credentials_required")
        self._token = ""
        self._client = client or httpx.Client(timeout=timeout_seconds)

    def close(self) -> None:
        self._client.close()

    def upload_resume(self, path: Path, idempotency_key: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/candidate-intakes/uploads",
            headers={"Idempotency-Key": idempotency_key},
            files={"file": (path.name, path.read_bytes(), "application/pdf")},
        )

    def upload_candidate_document(
        self,
        *,
        candidate_id: str,
        path: Path,
        display_name: str,
        category: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/candidates/{candidate_id}/documents",
            headers={"Idempotency-Key": idempotency_key},
            data={
                "displayName": display_name,
                "category": category,
                "sourceStage": "import",
                "note": "上游候选人文件夹导入",
            },
            files={"file": (path.name, path.read_bytes(), "application/pdf")},
        )

    def _login(self) -> None:
        log_event(logging.INFO, "backend_login_started", username=self.username)
        response = self._client.post(
            f"{self.base_url}/auth/login",
            json={"username": self.username, "password": self.password},
        )
        self._raise_for_status(response)
        token = str(response.json().get("accessToken") or "")
        if not token:
            raise BackendRequestError("backend_login_response_missing_access_token")
        self._token = token
        log_event(logging.INFO, "backend_login_completed", username=self.username)

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        if not self._token:
            self._login()
        response = self._authorized_request(method, path, **kwargs)
        if response.status_code == 401:
            log_event(
                logging.WARNING,
                "backend_token_refresh",
                method=method,
                path=path,
            )
            self._login()
            response = self._authorized_request(method, path, **kwargs)
        self._raise_for_status(response)
        return response.json()

    def _authorized_request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        headers = dict(kwargs.pop("headers", {}))
        headers["Authorization"] = f"Bearer {self._token}"
        return self._client.request(
            method, f"{self.base_url}{path}", headers=headers, **kwargs
        )

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.is_success:
            return
        message = response.text[:1000]
        try:
            payload = response.json()
            message = str(payload.get("message") or payload.get("detail") or message)
        except ValueError:
            pass
        log_event(
            logging.ERROR,
            "backend_request_failed",
            method=response.request.method,
            path=response.request.url.path,
            statusCode=response.status_code,
            error=message,
        )
        raise BackendRequestError(
            f"backend_request_failed:{response.status_code}:{message}"
        )
