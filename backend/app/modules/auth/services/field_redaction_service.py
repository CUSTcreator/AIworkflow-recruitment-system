"""候选人 DTO 的复制适配器。

资源可见性已由 AuthorizationService / ScopeService 统一判断。当前交付规则中，申请可见
即允许查看完整候选资料，因此这里保留副本隔离，不再依据独立权限码裁剪字段。
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from backend.app.modules.auth.domain.access_context import AccessContext


class FieldRedactionService:
    """返回 DTO 副本，避免响应组装过程修改 ORM payload 或原始 DTO。"""

    def redact_candidate_details(self, context: AccessContext, dto: dict[str, Any]) -> dict[str, Any]:
        """申请可见时完整返回候选资料；上下文保留以兼容现有调用点。"""
        del context
        return deepcopy(dto)
