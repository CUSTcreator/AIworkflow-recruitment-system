"""Auth 对外稳定入口：跨模块调用必须经由此处的显式惰性导出。"""
from __future__ import annotations
from importlib import import_module

_LAZY_EXPORTS = {
    "ACTION_PERMISSIONS": ("backend.app.modules.auth.authorization", "ACTION_PERMISSIONS"),
    "FIRST_INTERVIEW_ACTIONS": ("backend.app.modules.auth.authorization", "FIRST_INTERVIEW_ACTIONS"),
    "PERMISSION_LABELS": ("backend.app.modules.auth.authorization", "PERMISSION_LABELS"),
    "PERMISSION_DEFINITIONS": ("backend.app.modules.auth.authorization", "PERMISSION_DEFINITIONS"),
    "PERMISSION_REQUIRED_BUSINESS_SCOPE": ("backend.app.modules.auth.authorization", "PERMISSION_REQUIRED_BUSINESS_SCOPE"),
    "RESPONSIBILITY_BUNDLES": ("backend.app.modules.auth.authorization", "RESPONSIBILITY_BUNDLES"),
    "ROLE_DEFAULT_RESPONSIBILITY_BUNDLES": ("backend.app.modules.auth.authorization", "ROLE_DEFAULT_RESPONSIBILITY_BUNDLES"),
    "ROLE_DEFAULT_PERMISSIONS": ("backend.app.modules.auth.authorization", "ROLE_DEFAULT_PERMISSIONS"),
    "SECOND_INTERVIEW_ACTIONS": ("backend.app.modules.auth.authorization", "SECOND_INTERVIEW_ACTIONS"),
    "assert_business_action": ("backend.app.modules.auth.authorization", "assert_business_action"),
    "assert_permission": ("backend.app.modules.auth.authorization", "assert_permission"),
    "can_business_action": ("backend.app.modules.auth.authorization", "can_business_action"),
    "effective_permissions": ("backend.app.modules.auth.authorization", "effective_permissions"),
    "has_permission": ("backend.app.modules.auth.authorization", "has_permission"),
    "permission_overrides": ("backend.app.modules.auth.authorization", "permission_overrides"),
    "scope_allows": ("backend.app.modules.auth.authorization", "scope_allows"),
    "expand_responsibility_bundles": ("backend.app.modules.auth.authorization", "expand_responsibility_bundles"),
    "canonical_responsibility_bundle_code": ("backend.app.modules.auth.authorization", "canonical_responsibility_bundle_code"),
    "canonical_responsibility_bundle_codes": ("backend.app.modules.auth.authorization", "canonical_responsibility_bundle_codes"),
    "get_current_user": ("backend.app.modules.auth.http.dependencies", "get_current_user"),
    "require_system_admin": ("backend.app.modules.auth.http.dependencies", "require_system_admin"),
    "AuthorizationService": ("backend.app.modules.auth.services", "AuthorizationService"),
    "FieldRedactionService": ("backend.app.modules.auth.services", "FieldRedactionService"),
    "ScopeService": ("backend.app.modules.auth.services", "ScopeService"),
    "DataScope": ("backend.app.modules.auth.services.scope_service", "DataScope"),
}

def __getattr__(name: str):
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = target
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value

__all__ = list(_LAZY_EXPORTS)
