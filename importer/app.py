from __future__ import annotations

import hmac
import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Response, status

from importer.backend_client import RecruitmentBackendClient
from importer.config import ImporterConfig, load_config
from importer.contracts import (
    ImportBatchAcceptedView,
    ImportBatchRequest,
    ImportBatchStatusView,
)
from importer.event_log import configure_logging, log_event
from importer.import_manager import ImportManager
from importer.processor import CandidateFolderProcessor


def create_app(
    *,
    config: ImporterConfig | None = None,
    backend: RecruitmentBackendClient | None = None,
    api_key: str | None = None,
) -> FastAPI:
    configure_logging()
    resolved_config = config or load_config()
    resolved_backend = backend or RecruitmentBackendClient(
        resolved_config.backend_base_url,
        resolved_config.request_timeout_seconds,
    )
    manager = ImportManager(CandidateFolderProcessor(resolved_config, resolved_backend))
    resolved_api_key = api_key or os.getenv("IMPORTER_API_KEY", "")
    if not resolved_api_key:
        raise RuntimeError("importer_api_key_required")

    @asynccontextmanager
    async def lifespan(_application: FastAPI):
        yield
        manager.close()
        resolved_backend.close()

    application = FastAPI(
        title="Recruitment Candidate Importer",
        version="1.0.0",
        lifespan=lifespan,
    )

    def require_api_key(
        provided: str | None = Header(default=None, alias="X-Importer-Api-Key"),
    ) -> None:
        if provided is None or not hmac.compare_digest(provided, resolved_api_key):
            log_event(logging.WARNING, "api_auth_rejected")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Importer API Key 无效",
            )

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ready"}

    @application.post(
        "/api/v1/imports",
        response_model=ImportBatchAcceptedView,
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(require_api_key)],
    )
    def submit_import(
        payload: ImportBatchRequest,
        response: Response,
    ) -> ImportBatchAcceptedView:
        result = manager.submit(payload.folders)
        if not result.accepted:
            response.status_code = status.HTTP_200_OK
        return result

    @application.get(
        "/api/v1/imports/{request_id}",
        response_model=ImportBatchStatusView,
        dependencies=[Depends(require_api_key)],
    )
    def import_status(request_id: str) -> ImportBatchStatusView:
        result = manager.status(request_id)
        if result is None:
            log_event(
                logging.WARNING,
                "batch_status_not_found",
                requestId=request_id,
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="导入请求不存在或状态记录已过期",
            )
        return result

    application.state.import_manager = manager
    return application
