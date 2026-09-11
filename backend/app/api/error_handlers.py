"""全局 HTTP 错误投影。

本层只把异常转换为安全、稳定的 API 错误 DTO；它不决定领域状态转换，也不承担
Workflow 重试。同步命令的回滚和技术日志由 CommandRunner 先完成后再到这里。
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from backend.app.api.error_contracts import ApiErrorResponse
from backend.app.infrastructure.command_runtime.command_errors import CommandError
from backend.app.infrastructure.observability.logging import log_event
from backend.app.shared.errors import BusinessError, ExternalServiceError

logger = logging.getLogger(__name__)


def _response(
    request: Request, *, status_code: int, code: str, message: str,
    retryable: bool = False, action: str = "none", context: dict | None = None,
) -> JSONResponse:
    """所有 HTTP 异常都使用相同字段，前端无需读取框架原生错误文本。"""
    payload = ApiErrorResponse(
        code=code, message=message, retryable=retryable, action=action,
        request_id=getattr(request.state, "request_id", None), context=context or {},
    )
    return JSONResponse(status_code=status_code, content=payload.as_payload())


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(CommandError)
    async def handle_command_error(request: Request, error: CommandError) -> JSONResponse:
        log_event(
            logger, logging.WARNING if error.status_code < 500 else logging.ERROR,
            "http_command_error", error_code=error.code, status_code=error.status_code,
            retryable=error.retryable, action=error.action, route=request.url.path,
        )
        return _response(
            request, status_code=error.status_code, code=error.code, message=error.message,
            retryable=error.retryable, action=error.action, context=error.context,
        )

    @app.exception_handler(BusinessError)
    async def handle_business_error(request: Request, error: BusinessError) -> JSONResponse:
        # 查询接口和未迁移命令也共享同一 DTO；业务规则本身不应被伪装为系统故障。
        action = "refresh" if error.status_code == 409 else "none"
        log_event(logger, logging.WARNING, "http_business_error", error_code=error.code,
                  status_code=error.status_code, route=request.url.path)
        return _response(
            request, status_code=error.status_code, code=error.code, message=error.message,
            action=action, context=error.context,
        )

    @app.exception_handler(ExternalServiceError)
    async def handle_external_service_error(request: Request, error: ExternalServiceError) -> JSONResponse:
        status_code = 503 if error.retryable else 502
        log_event(logger, logging.ERROR, "http_external_service_error", error_code=error.code,
                  service=error.service, retryable=error.retryable, status_code=status_code,
                  route=request.url.path)
        return _response(
            request, status_code=status_code, code=error.code, message=error.message,
            # 外部服务即使本次不可重试，用户仍可在服务恢复后重新提交；不要把
            # 业务流程停在“联系管理员”的死路上。
            retryable=True, action="retry",
            context={**error.context, "service": error.service},
        )

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation_error(request: Request, _: RequestValidationError) -> JSONResponse:
        log_event(logger, logging.WARNING, "http_request_validation_failed", route=request.url.path)
        return _response(
            request, status_code=422, code="request_validation_failed", message="请求参数校验失败",
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, error: Exception) -> JSONResponse:
        logger.exception("http_unhandled_exception", extra={"event": "http_unhandled_exception", "route": request.url.path})
        return _response(
            request, status_code=500, code="internal_server_error", message="服务器内部错误",
            retryable=True, action="retry",
        )
