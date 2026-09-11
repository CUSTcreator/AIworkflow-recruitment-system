"""授权判断使用的不可变访问上下文。

上下文只携带已经解析好的身份、有效权限和资源归属；纯策略函数不再直接依赖 ORM，
因此能在 HTTP 请求、后台 Command 和单元测试中使用同一套规则。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AccessContext:
    """一个已认证主体的授权快照，不携带密码、令牌或 Session。"""

    user_id: str
    is_active: bool
    business_scope: str
    department_id: str | None
    permissions: frozenset[str]
    is_system_admin: bool = False


@dataclass(frozen=True, slots=True)
class ApplicationAccessContext:
    """执行 Application 级动作所需的最小资源信息。"""

    application_id: str
    department_id: str | None
    assigned_first_interviewer: str | None
    assigned_hr: str | None
    status: str | None = None
