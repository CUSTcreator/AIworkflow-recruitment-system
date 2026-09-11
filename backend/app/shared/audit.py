from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from backend.app.infrastructure.observability.context import current_context
from backend.app.models.entities import AuditEvent, User


def record_audit_event(
    db: Session,
    *,
    actor: User | None,
    action: str,
    target_type: str,
    target_id: str,
    summary: str,
    details: dict[str, Any] | None = None,
    workflow_run_id: str | None = None,
) -> AuditEvent:
    """写入业务审计，不替代技术日志或工作流时间线。

    调用方传入业务动作和面向管理员的安全摘要；请求、工作流关联字段从当前
    ContextVar 自动继承。不要将原始异常、简历文本、提示词或令牌放入 details。
    本函数只 ``add``，由调用它的业务事务统一提交。
    """
    correlation = current_context()
    event = AuditEvent(
        audit_event_id=f"AUD_{uuid.uuid4().hex[:20].upper()}",
        actor_user_id=actor.user_id if actor else None,
        actor_name=actor.display_name if actor else "系统",
        action=action,
        target_type=target_type,
        target_id=target_id,
        summary=summary[:500],
        details=details or {},
        request_id=correlation.get("request_id"),
        workflow_run_id=workflow_run_id or correlation.get("workflow_run_id"),
    )
    db.add(event)
    return event