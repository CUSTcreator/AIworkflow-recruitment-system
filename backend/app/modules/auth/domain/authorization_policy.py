"""纯授权规则。

这里的函数只根据 AccessContext 和资源上下文给出允许/拒绝结论，不抛 HTTP 异常、
不访问数据库。AuthorizationService 负责把拒绝转换为统一的 BusinessError。
"""
from __future__ import annotations

from dataclasses import dataclass

from .access_context import AccessContext, ApplicationAccessContext
from .permission_catalog import (
    PERMISSION_REQUIRED_BUSINESS_SCOPE,
    application_action_requirement,
)


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    """授权结果及稳定拒绝码，供 API、审计和测试使用。"""

    allowed: bool
    reason_code: str = ""


def scope_allows(context: AccessContext, department_id: str | None) -> bool:
    """判断主体的数据范围是否覆盖指定部门。"""
    if context.business_scope == "organization":
        return True
    return bool(department_id and context.department_id and department_id == context.department_id)


def can_business_action(
    context: AccessContext,
    permission_code: str,
    *,
    department_id: str | None = None,
    require_organization_scope: bool = False,
) -> AuthorizationDecision:
    """判断不依赖具体 Application 分配关系的通用业务权限。"""
    if not context.is_active:
        return AuthorizationDecision(False, "account_inactive")
    if permission_code not in context.permissions:
        return AuthorizationDecision(False, "permission_missing")
    # 原子权限可以声明自己的最低范围。职责包只负责组合权限，不能把
    # 全公司配置权限错误地降级为本部门操作，也不能连带限制普通岗位操作。
    required_scope = PERMISSION_REQUIRED_BUSINESS_SCOPE.get(permission_code)
    if required_scope == "organization" and context.business_scope != "organization":
        return AuthorizationDecision(False, "organization_scope_required")
    if require_organization_scope and context.business_scope != "organization":
        return AuthorizationDecision(False, "organization_scope_required")
    if department_id is not None and not scope_allows(context, department_id):
        return AuthorizationDecision(False, "department_scope_forbidden")
    return AuthorizationDecision(True)


def can_view_application(
    context: AccessContext,
    application: ApplicationAccessContext,
) -> AuthorizationDecision:
    """判断申请可见性：账号有效且数据范围覆盖申请所属部门。

    查看不是职责包能力，不读取原子权限或个人例外。组织范围可见全部，部门
    范围可见本部门全部；不能再用单独的查看开关把同部门记录拆成个人范围，
    以免详情、列表和纯恢复动作得到不一致的可见性结论。
    """
    if not context.is_active:
        return AuthorizationDecision(False, "account_inactive")
    if not scope_allows(context, application.department_id):
        return AuthorizationDecision(False, "department_scope_forbidden")
    return AuthorizationDecision(True)


def can_view_application_material(
    context: AccessContext,
    application: ApplicationAccessContext,
) -> AuthorizationDecision:
    """候选材料随申请可见性继承，不再配置额外的完整资料权限。"""
    return can_view_application(context, application)


def can_application_action(
    context: AccessContext,
    application: ApplicationAccessContext,
    action: str,
) -> AuthorizationDecision:
    """判断 Application 动作的可见性、职责权限与负责人限制。

    所有申请上的写操作都以该申请可见为前置条件。可见性仅由账号有效状态
    和数据范围决定，因此任何职责包都不能越过部门边界修改申请。纯恢复动作
    在此基础上不再要求额外职责；会改变业务结果的动作仍需对应职责和必要的
    负责人身份。
    """
    requirement = application_action_requirement(
        action,
        application_status=application.status,
    )
    if requirement is None:
        return AuthorizationDecision(False, "permission_not_defined")

    # 统一公式：敏感申请动作 = 申请可见 + 对应职责 + 可选负责人限制。
    # 先检查可见性也让所有拒绝路径保持一致，避免权限覆盖形成越权写入口。
    visibility = can_view_application(context, application)
    if not visibility.allowed:
        return visibility

    # 页面会先按状态产生候选动作；这里再次校验接口级允许状态，防止客户端
    # 绕过页面，拿其他阶段的决策接口执行同名 reject/hold 等状态机动作。
    if requirement.allowed_statuses and application.status not in requirement.allowed_statuses:
        return AuthorizationDecision(False, "action_not_available_in_status")

    # 纯恢复动作复用完整的申请可见性，不能因知道 Application ID 而绕过
    # 数据范围；它们不会推进主流程、提交人工结论或修改招聘结果。
    if (
        requirement.authorization_mode == "visible_retry"
        or application.status in requirement.visibility_retry_statuses
    ):
        return can_view_application(context, application)

    if requirement.permission_code is None:
        return AuthorizationDecision(False, "permission_not_defined")

    base = can_business_action(
        context,
        requirement.permission_code,
        department_id=application.department_id,
    )
    if not base.allowed:
        return base
    if requirement.assignment == "first_interviewer" and context.user_id != application.assigned_first_interviewer:
        return AuthorizationDecision(False, "first_interviewer_not_assigned")
    if requirement.assignment == "hr" and context.user_id != application.assigned_hr:
        return AuthorizationDecision(False, "hr_not_assigned")
    return AuthorizationDecision(True)
