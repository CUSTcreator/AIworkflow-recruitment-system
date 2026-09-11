"""同步命令层的错误合同。

本文件只描述 HTTP 同步写命令在事务边界上的失败语义：错误码、是否可重试和
前端建议动作。它不替代领域 ``BusinessError``，也不负责 Workflow Step 的重试。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


CommandErrorAction = Literal["none", "refresh", "retry"]


@dataclass(slots=True)
class CommandError(Exception):
    """已经脱敏、可安全投影给 HTTP 层的同步命令失败。"""

    code: str
    message: str
    status_code: int
    retryable: bool
    action: CommandErrorAction
    context: dict[str, object]

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int,
        retryable: bool = False,
        action: CommandErrorAction = "none",
        context: dict[str, object] | None = None,
    ) -> None:
        Exception.__init__(self, message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable
        self.action = action
        self.context = dict(context or {})


def command_error_from_exception(error: Exception) -> CommandError:
    """把未预期技术异常收敛为不泄露实现细节的命令错误。"""
    return CommandError(
        "command_internal_failed",
        "操作未保存，系统发生未预期错误。可稍后重新提交，系统会保留已成功的操作。",
        status_code=500,
        retryable=True,
        action="retry",
    )
