"""命令执行管道的稳定合同。

业务模块通过 CommandSpec 声明所需权限、资源类型、幂等和审计要求；它们不需要了解
IdempotencyKey 表、审计表或 HTTP 细节。当前骨架只提供合同，迁移时再逐个接入现有命令。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from sqlalchemy.orm import Session

from backend.app.models.entities import User


ResourceType = Literal["application", "job", "candidate", "resume_submission", "source_document", "job_draft", "user", "role", "department", "workflow_run", "interview_guide_template", "upload", "system"]
AuthenticationMode = Literal["authenticated", "anonymous_authentication"]
CommandAuthorizationMode = Literal["business_permission", "authenticated_self"]


@dataclass(frozen=True, slots=True)
class AuditSpec:
    """命令成功后写入的业务审计声明；审计与业务修改必须同一事务提交。"""

    action: str
    target_type: str
    summary: str


@dataclass(frozen=True, slots=True)
class CommandSpec:
    """一个同步写命令的横切要求，不包含状态机或领域业务实现。

    执行顺序由 `CommandRunner` 固定为：锁资源 → 鉴权 → 幂等 → handler →
    审计/幂等账本 → 原子提交。领域 Service 只改当前 Session，不能自行 commit。
    Application 和 ResumeSubmission 命令还必须把 ``authorization_action`` 映射到
    权限目录；不能仅凭调用方传入的任意权限码绕过资源可见性或负责人限制。
    """

    # action 用于幂等和审计；authorization_action 为空时才与 action 相同。
    # 例如 final_decision 是命令名，offer / reject 才是实际授权动作。
    action: str
    resource_type: ResourceType
    permission_code: str | None
    # 常规写命令必须具备当前登录用户。仅登录命令可声明 anonymous_authentication，
    # 且该模式只允许资源类型为 system、不得写入常规业务审计。
    authentication_mode: AuthenticationMode = "authenticated"
    # ``authenticated_self`` 只服务于当前账号自己的轻量状态（如已读游标）。
    # CommandRunner 会强制 system 资源、resource_id 等于 user_id、无业务权限码、
    # 非幂等且审计豁免，不能借此模式读写业务资源或绕过职责权限。
    authorization_mode: CommandAuthorizationMode = "business_permission"
    authorization_action: str | None = None
    # "application_id" 用于迁移既有 ApplicationCommandExecutor 时保持历史幂等指纹。
    idempotency_resource_key: str = "resource_id"
    idempotent: bool = True
    require_organization_scope: bool = False
    # 系统管理是账号身份授权，不属于可配置的业务权限。
    requires_system_admin: bool = False
    # 未显式声明时 CommandRunner 会按 action/resource 生成安全的默认审计事件。
    # 只有个人已读游标等明确豁免的轻量命令才可以设置 audit_exempt。
    audit: AuditSpec | None = None
    audit_exempt: bool = False


@dataclass(slots=True)
class CommandContext:
    """传给业务 handler 的受控上下文；handler 不得自行提交事务。"""

    db: Session
    # 登录等认证前命令没有当前 User；业务 handler 必须按自己的 spec 判断是否允许。
    user: User | None
    spec: CommandSpec
    resource_id: str
    resource: Any
    body: dict[str, Any] = field(default_factory=dict)
    compensations: list[Callable[[], None]] = field(default_factory=list)
    post_commit_callbacks: list[Callable[[], None]] = field(default_factory=list)

    def after_commit(self, operation: Callable[[], None]) -> None:
        """Register irreversible external cleanup to run only after DB commit."""
        self.post_commit_callbacks.append(operation)

    def compensate_on_rollback(self, operation: Callable[[], None]) -> None:
        """登记数据库事务失败后的外部活动补偿（例如删除刚上传的对象）。"""
        self.compensations.append(operation)


class CommandHandler(Protocol):
    """业务命令处理器协议：只修改本次事务中的领域对象并返回页面响应。"""

    def __call__(self, context: CommandContext) -> dict[str, Any]: ...


ResourceLoader = Callable[[Session, str], Any]
