from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.app.core.config import settings

from .context import current_context
from .redaction import redact


_STANDARD_RECORD_FIELDS = frozenset(logging.makeLogRecord({}).__dict__) | {
    "message", "asctime", "taskName",
}
_LOG_TIMEZONE = timezone(timedelta(hours=8), "Asia/Shanghai")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            # 数据库和调度仍统一使用 UTC；面向本地运维人员的日志使用明确的
            # +08:00 偏移，避免把合法 UTC 时间误读为慢了 8 小时。
            "timestamp": datetime.fromtimestamp(
                record.created, tz=_LOG_TIMEZONE
            ).isoformat(),
            "timezone": "Asia/Shanghai",
            "level": record.levelname,
            "service": settings.observability_service_name,
            "environment": settings.app_env,
            "logger": record.name,
            "message": record.getMessage(),
            **current_context(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_FIELDS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(redact(payload), ensure_ascii=False, default=str)


def configure_logging() -> None:
    """Configure process-wide stdout JSON logging once per Backend/Worker process."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.handlers[:] = [handler]


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_event(logger: logging.Logger, level: int, event: str, /, **fields: Any) -> None:
    logger.log(level, event, extra={"event": event, **fields})
