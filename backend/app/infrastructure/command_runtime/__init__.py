"""同步业务 Command 的统一执行运行时。

该包是授权、幂等、审计和事务提交的组合边界；它与 workflow_runtime 并列：
前者处理用户发起的同步命令，后者处理异步 Workflow 的步骤恢复。
"""

from .command_contracts import AuditSpec, CommandContext, CommandSpec
from .command_runner import CommandRunner
from .idempotency_guard import IdempotencyGuard

__all__ = ["AuditSpec", "CommandContext", "CommandRunner", "CommandSpec", "IdempotencyGuard"]