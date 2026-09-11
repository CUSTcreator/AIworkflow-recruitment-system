"""数据库适配器的低噪声运行日志。"""
from __future__ import annotations

from hashlib import sha256
from time import perf_counter
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Engine

from backend.app.core.config import settings
from backend.app.infrastructure.observability.logging import get_logger, log_event
from backend.app.infrastructure.observability.persistence_errors import describe_persistence_error

logger = get_logger(__name__)


def _fingerprint(statement: str) -> str:
    normalized = " ".join(statement.split())
    return sha256(normalized.encode("utf-8")).hexdigest()[:16]


def install_database_observability(engine: Engine) -> None:
    """安装一次慢查询/适配器错误观测，不记录 SQL 参数和结果数据。"""
    if getattr(engine, "_recruit_observability_installed", False):
        return
    setattr(engine, "_recruit_observability_installed", True)

    # SQLAlchemy 事件只统计耗时和错误类型。SQL 参数可能含候选人信息，因此绝不记录。
    @event.listens_for(engine, "before_cursor_execute")
    def before_cursor_execute(conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool) -> None:
        context._recruit_query_started_at = perf_counter()

    @event.listens_for(engine, "after_cursor_execute")
    def after_cursor_execute(conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool) -> None:
        started = getattr(context, "_recruit_query_started_at", None)
        if started is None:
            return
        duration_ms = round((perf_counter() - started) * 1000, 2)
        # 只让超过阈值的查询进入 stdout，避免正常 SQL 形成高噪声日志。
        if duration_ms >= settings.database_slow_query_ms:
            log_event(logger, 30, "database_query_slow", duration_ms=duration_ms, statement_fingerprint=_fingerprint(statement), dialect=engine.dialect.name)

    # 适配器异常同样不写数据库，避免错误处理自身再触发事务失败。
    @event.listens_for(engine, "handle_error")
    def handle_error(exception_context: Any) -> None:
        statement = exception_context.statement or ""
        # 只记录驱动提供的脱敏元数据；SQL 参数可能包含候选人信息，禁止写入日志。
        details = describe_persistence_error(exception_context.sqlalchemy_exception)
        log_event(
            logger, 40, "database_query_failed",
            dialect=engine.dialect.name,
            statement_fingerprint=_fingerprint(statement),
            error_type=type(exception_context.original_exception).__name__,
            **details,
        )