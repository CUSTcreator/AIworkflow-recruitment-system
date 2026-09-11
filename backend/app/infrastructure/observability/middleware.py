from __future__ import annotations

import logging
import re
import uuid
from time import perf_counter

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from .context import bind_context
from .logging import log_event
from .metrics import metrics


logger = logging.getLogger(__name__)
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


class RequestObservabilityMiddleware(BaseHTTPMiddleware):
    """HTTP 日志边界：创建 request_id，并在请求结束时只记录安全的摘要。"""
    async def dispatch(self, request: Request, call_next):
        candidate = request.headers.get("X-Request-ID", "")
        correlation_id = candidate if _SAFE_REQUEST_ID.fullmatch(candidate) else f"REQ_{uuid.uuid4().hex[:16].upper()}"
        request.state.request_id = correlation_id
        started = perf_counter()
        status_code = 500
        # request_id 进入 ContextVar 后，当前请求链路中所有 JSON 日志会自动带上它。
        # 不能在这里记录 body、查询参数或身份令牌，避免将简历和凭据写入运行日志。
        with bind_context(request_id=correlation_id):
            try:
                response = await call_next(request)
                status_code = response.status_code
                return response
            finally:
                duration_ms = round((perf_counter() - started) * 1000, 2)
                route = request.scope.get("route")
                route_path = getattr(route, "path", request.url.path)
                # 单条“请求完成”事件即可表达 HTTP 可用性；异常细节由错误处理器和
                # 技术日志排障，不能回写到业务审计或前端执行时间线。
                log_event(
                    logger,
                    logging.INFO if status_code < 500 else logging.ERROR,
                    "http_request_completed",
                    method=request.method,
                    route=route_path,
                    status_code=status_code,
                    duration_ms=duration_ms,
                )
                metrics.increment("recruit_http_requests", method=request.method, route=route_path, status=str(status_code))
                if status_code >= 400:
                    metrics.increment("recruit_http_errors", method=request.method, route=route_path, status=str(status_code))
                # Starlette's response may be unavailable on an exception; handlers still receive the ID from context.
                if "response" in locals():
                    response.headers["X-Request-ID"] = correlation_id
