"""显式惰性依赖：用于打破启动期的模块循环，同时保留模块级依赖声明。"""
from __future__ import annotations

from importlib import import_module
from typing import Any


class LazyCallable:
    """首次调用时解析目标函数的模块级依赖代理。

    业务模块只能在模块顶部创建该代理，不能在业务函数体中临时 import。
    因此依赖目标可通过源码审计，且不会在应用启动时触发完整的下游服务树。
    """

    def __init__(self, module_name: str, attribute_name: str) -> None:
        self.module_name = module_name
        self.attribute_name = attribute_name

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        target = getattr(import_module(self.module_name), self.attribute_name)
        return target(*args, **kwargs)