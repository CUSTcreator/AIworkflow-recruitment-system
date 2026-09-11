"""工作流注册表：显式映射持久化类型及其版本化步骤定义。"""
from __future__ import annotations

from .contracts import WorkflowSpec


class WorkflowRegistry:
    """持久化 workflow_type/definition_version 到业务处理器的显式映射。"""

    def __init__(self) -> None:
        self._specs: dict[str, dict[int, WorkflowSpec]] = {}

    def register(self, spec: WorkflowSpec) -> None:
        versions = self._specs.setdefault(spec.workflow_type, {})
        current = versions.get(spec.definition_version)
        if current is not None and current != spec:
            raise RuntimeError(f"duplicate_workflow_spec:{spec.workflow_type}:v{spec.definition_version}")
        versions[spec.definition_version] = spec

    def get(self, workflow_type: str | None, definition_version: int | None = None) -> WorkflowSpec | None:
        versions = self._specs.get(workflow_type or "")
        if not versions:
            return None
        if definition_version is None:
            return versions[max(versions)]
        return versions.get(definition_version)

    def require(self, workflow_type: str | None, definition_version: int | None = None) -> WorkflowSpec:
        spec = self.get(workflow_type, definition_version)
        if spec is None:
            suffix = "" if definition_version is None else f":v{definition_version}"
            raise RuntimeError(f"unsupported_workflow_type:{workflow_type}{suffix}")
        return spec

    def supports(self, workflow_type: str | None, definition_version: int | None) -> bool:
        """Return whether a persisted workflow definition can still run in this deployment.

        A workflow type alone is insufficient: a queued historical run may reference a
        retired step plan. Callers that claim persisted runs must check this exact pair
        before giving the run a lease.
        """
        return self.get(workflow_type, definition_version) is not None

    def types(self) -> frozenset[str]:
        return frozenset(self._specs)
