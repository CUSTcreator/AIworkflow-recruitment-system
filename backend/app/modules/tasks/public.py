"""Tasks 对外稳定入口：跨模块调用必须经由此处的显式惰性导出。"""
from __future__ import annotations
from importlib import import_module

_LAZY_EXPORTS = {"TaskWriteService": ("backend.app.modules.tasks.write_service", "TaskWriteService")}

def __getattr__(name: str):
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = target
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value

__all__ = list(_LAZY_EXPORTS)