"""数据库异常的脱敏诊断提取器。

SQLAlchemy/psycopg 的异常对象结构不同于 HTTP 或 LLM 回包，不能交给
ExternalActivity 处理。本模块只读取驱动提供的 SQLSTATE、约束名和表列名，
绝不记录 SQL 参数、简历正文或其它敏感内容。
"""
from __future__ import annotations

from typing import Any


def describe_persistence_error(error: BaseException) -> dict[str, Any]:
    """返回可安全写入技术日志和执行时间线的数据库诊断字段。"""
    original = getattr(error, "orig", None) or error
    diagnostic = getattr(original, "diag", None)

    def read(name: str) -> str | None:
        value = getattr(original, name, None)
        if value is None and diagnostic is not None:
            value = getattr(diagnostic, name, None)
        return str(value)[:160] if value is not None else None

    return {
        "sqlstate": read("sqlstate") or read("pgcode"),
        "constraint_name": read("constraint_name"),
        "table_name": read("table_name"),
        "column_name": read("column_name"),
    }
