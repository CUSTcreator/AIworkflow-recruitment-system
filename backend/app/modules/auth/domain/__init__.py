"""授权领域合同。

这里仅保存权限目录、访问上下文和纯授权规则，不访问 HTTP、数据库或具体业务模块。
这样权限规则可以被接口、命令、查询和测试共同复用。
"""

from .access_context import AccessContext, ApplicationAccessContext
from .permission_catalog import (
    ACTION_PERMISSIONS,
    APPLICATION_ACTION_REQUIREMENTS,
    PERMISSION_DEFINITIONS,
    PERMISSION_LABELS,
    PERMISSION_REQUIRED_BUSINESS_SCOPE,
    RESPONSIBILITY_BUNDLES,
    ROLE_DEFAULT_RESPONSIBILITY_BUNDLES,
    ROLE_DEFAULT_PERMISSIONS,
    expand_responsibility_bundles,
    canonical_responsibility_bundle_code,
    canonical_responsibility_bundle_codes,
)
from .authorization_policy import AuthorizationDecision, can_application_action, can_business_action

__all__ = [
    "ACTION_PERMISSIONS", "APPLICATION_ACTION_REQUIREMENTS",
    "PERMISSION_DEFINITIONS", "PERMISSION_LABELS", "PERMISSION_REQUIRED_BUSINESS_SCOPE",
    "RESPONSIBILITY_BUNDLES", "ROLE_DEFAULT_RESPONSIBILITY_BUNDLES",
    "ROLE_DEFAULT_PERMISSIONS", "expand_responsibility_bundles",
    "canonical_responsibility_bundle_code", "canonical_responsibility_bundle_codes",
    "AccessContext", "ApplicationAccessContext",
    "AuthorizationDecision", "can_application_action", "can_business_action",
]
