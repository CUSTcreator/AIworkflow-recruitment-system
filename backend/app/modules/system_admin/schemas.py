from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


BusinessScope = Literal["department", "organization"]
PermissionEffect = Literal["allow", "deny"]


class AdminUserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=2, max_length=64)
    displayName: str = Field(min_length=1, max_length=128)
    roleId: str = Field(min_length=1, max_length=64)
    departmentId: str | None = Field(default=None, max_length=64)
    businessScope: BusinessScope = "department"
    # 新管理端按职责包保存个人例外；后端会在写库前展开为原子权限码。
    responsibilityOverrides: dict[str, PermissionEffect] = Field(default_factory=dict)
    # 兼容旧客户端。新客户端不应再提交原子权限覆盖。
    permissionOverrides: dict[str, PermissionEffect] = Field(default_factory=dict)
    isSystemAdmin: bool = False
    password: str = Field(min_length=8, max_length=128)


class AdminUserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str | None = Field(default=None, min_length=2, max_length=64)
    displayName: str | None = Field(default=None, min_length=1, max_length=128)
    roleId: str | None = Field(default=None, min_length=1, max_length=64)
    departmentId: str | None = Field(default=None, max_length=64)
    businessScope: BusinessScope | None = None
    responsibilityOverrides: dict[str, PermissionEffect] | None = None
    # 兼容旧客户端；职责包字段优先于此字段。
    permissionOverrides: dict[str, PermissionEffect] | None = None
    isSystemAdmin: bool | None = None
    isActive: bool | None = None


class PasswordResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str = Field(min_length=8, max_length=128)


class RoleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    businessScope: BusinessScope
    # 角色配置只暴露职责包，持久化时由服务统一展开为原子权限码。
    responsibilityBundles: list[str] | None = None
    # 兼容旧客户端创建的原子权限角色；新客户端不应再使用。
    permissions: list[str] | None = None


class RoleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=128)
    businessScope: BusinessScope | None = None
    responsibilityBundles: list[str] | None = None
    # 兼容旧客户端更新的原子权限角色；职责包字段优先于此字段。
    permissions: list[str] | None = None
    isActive: bool | None = None


class DepartmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)


class DepartmentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=128)


class AdminJobUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # setup_pending 表示 JD 已确认、允许接收候选人分发，但面试组织配置尚未完成。
    # 它是正常业务状态，系统管理的 PATCH 必须能够原样传递该值。
    status: Literal["setup_pending", "open", "closed"] | None = None
    headcount: int | None = Field(default=None, ge=1, le=100_000)
    hiringManagerId: str | None = Field(default=None, max_length=64)
    departmentRecruiterId: str | None = Field(default=None, max_length=64)


class WorkflowRetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=500)


# 以下 DTO 是系统管理“后台任务”读模型的公开合同。它们只暴露可安全展示的
# 运行状态和稳定错误码；原始异常、外部响应与诊断字段不属于管理页面的数据边界。
class AdminWorkflowView(BaseModel):
    workflowRunId: str
    workflowType: str
    applicationId: str | None = None
    subjectType: str | None = None
    subjectId: str | None = None
    status: str
    attemptCount: int
    maxAttempts: int
    startedAt: str | None = None
    completedAt: str | None = None
    updatedAt: str | None = None
    errorMessage: str = ""
    errorCode: str | None = None
    availableActions: list[Literal["retry", "resume"]] = Field(default_factory=list)
    currentStepName: str | None = None
    currentStepStatus: str | None = None
    nextAttemptAt: str | None = None
    # 每条后台任务的最新安全运行事件，列表可直接感知工作流的新进度。
    latestEventMessage: str | None = None
    latestEventSeverity: str | None = None
    latestEventAt: str | None = None


class AdminWorkflowPage(BaseModel):
    items: list[AdminWorkflowView]
    nextCursor: str | None = None


class WorkflowSummaryView(BaseModel):
    pending: int
    running: int
    blocked: int
    completed: int
    failed: int


class WorkflowExecutionEventView(BaseModel):
    executionEventId: str
    workflowRunId: str
    occurredAt: str
    stepName: str | None = None
    stepLabel: str | None = None
    eventType: str
    severity: str
    message: str
    attemptCount: int | None = None
    pollCount: int | None = None
    nextAttemptAt: str | None = None
    errorCategory: str | None = None
    errorCode: str | None = None


class WorkflowExecutionEventPage(BaseModel):
    items: list[WorkflowExecutionEventView]
    nextCursor: str | None = None


class ObjectCleanupTaskView(BaseModel):
    cleanupTaskId: str
    applicationId: str
    status: Literal["pending", "failed"]
    pendingObjectCount: int
    lastAttemptAt: str | None = None
    message: str


class AdminAuditEventView(BaseModel):
    auditEventId: str
    actorUserId: str | None = None
    actorName: str
    action: str
    targetType: str
    targetId: str
    summary: str
    details: dict[str, object] = Field(default_factory=dict)
    createdAt: str | None = None


class AdminAuditEventPage(BaseModel):
    items: list[AdminAuditEventView]
    nextCursor: str | None = None
