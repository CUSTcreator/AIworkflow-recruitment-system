"""同步 HTTP 写命令的统一事务边界。

本执行器负责资源锁、认证/授权、幂等、审计和一次原子提交。它不执行耗时的
Workflow Step；PDF、LLM 等异步工作由 Worker + StepRunner 处理。
"""
from __future__ import annotations

import logging
from time import perf_counter
from typing import Any

from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError
from sqlalchemy.orm import Session

from backend.app.infrastructure.command_runtime.command_contracts import AuditSpec, CommandContext, CommandHandler, CommandSpec, ResourceLoader
from backend.app.infrastructure.command_runtime.command_errors import CommandError, command_error_from_exception
from backend.app.infrastructure.command_runtime.idempotency_guard import IdempotencyGuard
from backend.app.infrastructure.observability.logging import log_event
from backend.app.infrastructure.observability.metrics import metrics
from backend.app.models.entities import Application, User
from backend.app.modules.auth.domain.permission_catalog import (
    APPLICATION_ACTION_REQUIREMENTS,
    RESUME_SUBMISSION_ACTION_REQUIREMENTS,
)
from backend.app.modules.auth.services.authorization_service import AuthorizationService
from backend.app.shared.audit import record_audit_event
from backend.app.shared.errors import BusinessError

logger = logging.getLogger(__name__)


class CommandRunner:
    """同步命令的唯一提交者。

    重要不变量：领域 Service 只能修改当前 Session；成功时业务数据、审计和幂等
    响应一起提交；失败时统一回滚。回滚不把业务对象改为 ``failed``，因为它表示
    “本次用户命令未保存”，而不是异步业务流程执行失败。
    """

    def __init__(self, db: Session) -> None:
        self.db = db
        self.authorization = AuthorizationService(db)
        self.idempotency = IdempotencyGuard(db)

    def execute(
        self,
        *,
        spec: CommandSpec,
        user: User | None,
        resource_id: str,
        body: dict[str, Any] | None,
        handler: CommandHandler,
        resource_loader: ResourceLoader | None = None,
        idempotency_key: str = "",
        idempotency_extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """执行一条同步命令，并把所有可失败阶段纳入同一事务边界。

        1. 加载/锁定资源；2. 校验命令身份并授权；3. 检查幂等重放；4. 调用领域
        handler；5. 写审计和幂等账本；6. 原子提交。任一步骤出错都回滚并上抛为
        ``CommandError``，由全局 HTTP 异常处理器转换为安全 DTO。
        """
        started = perf_counter()
        phase = "resource_loading"
        payload = dict(body or {})

        context: CommandContext | None = None
        resource: Any = None
        request_hash = ""
        try:
            # 1. 资源加载器负责行锁，防止两个写命令并发修改同一业务对象。
            resource = self._load_resource(spec, resource_id, resource_loader)

            # 2. 登录前认证命令是显式白名单；其余命令必须已有当前用户并完成授权。
            phase = "authorization"
            self._validate_authentication_mode(spec, user)
            self._authorize(spec, user, resource, resource_id)

            # 3. 仅已认证、声明幂等的写命令读取/写入 IdempotencyKey 账本。
            if spec.idempotent:
                phase = "idempotency_check"
                if user is None:
                    raise CommandError(
                        "command_internal_failed", "命令身份配置错误。", status_code=500,
                        retryable=True, action="retry",
                    )
                # 即使旧 Router 仍传入自己生成的 legacy 键，也在命令边界再收口一次。
                # 这保证最终写入 IdempotencyKey 的值永远满足数据库长度合同。
                idempotency_key = self.idempotency.compatibility_key(
                    idempotency_key or None, user_id=user.user_id,
                    action=spec.action, resource_id=resource_id, body=payload,
                )
                request_hash = self.idempotency.request_hash(
                    action=spec.action,
                    resource_id=resource_id,
                    body=payload,
                    resource_key=spec.idempotency_resource_key,
                    extra=idempotency_extra,
                )
                replay = self.idempotency.replay_or_none(
                    user=user, idempotency_key=idempotency_key, request_hash=request_hash,
                )
                if replay is not None:
                    self._log_success(spec, user, resource_id, started, replayed=True)
                    return replay

            # 4. handler 只产生本事务内的领域变更，禁止自行 commit。
            phase = "handler"
            context = CommandContext(
                db=self.db, user=user, spec=spec, resource_id=resource_id,
                resource=resource, body=payload,
            )
            response = handler(context)

            # 5. 成功审计与幂等响应必须和领域修改同一事务提交。
            phase = "publication"
            if not spec.audit_exempt:
                if user is None:
                    raise CommandError(
                        "command_internal_failed", "匿名命令不得写入业务审计。", status_code=500,
                        retryable=True, action="retry",
                    )
                audit = spec.audit or AuditSpec(
                    action=spec.action, target_type=spec.resource_type,
                    summary=f"执行命令：{spec.action}",
                )
                record_audit_event(
                    self.db, actor=user, action=audit.action, target_type=audit.target_type,
                    target_id=resource_id, summary=audit.summary,
                )
            if spec.idempotent:
                self.idempotency.remember_success(
                    user=user, idempotency_key=idempotency_key,
                    request_hash=request_hash, response=response,
                )

            # 6. 唯一的业务提交点。提交失败时下方会撤销前述全部领域修改。
            phase = "commit"
            self.db.commit()
        except Exception as error:
            self._rollback_and_compensate(context)
            mapped = self._map_error(error)
            event = "command_rejected" if mapped.status_code < 500 else "command_rolled_back"
            metrics.increment(
                "recruit_command_failure", command=spec.action, phase=phase,
                code=mapped.code, retryable=mapped.retryable,
            )
            log_event(
                logger, logging.WARNING if mapped.status_code < 500 else logging.ERROR, event,
                command=spec.action, resource_type=spec.resource_type, resource_id=resource_id,
                actor_id=user.user_id if user else "anonymous", phase=phase,
                code=mapped.code, error_type=type(error).__name__, rolled_back=True,
                # 技术日志保留已截断的异常摘要，便于定位已回滚的命令；HTTP 响应仍只返回安全错误合同。
                error_message=str(error)[:500],
                compensation_count=len(context.compensations) if context else 0,
                duration_ms=round((perf_counter() - started) * 1000, 2),
            )
            if mapped is error:
                raise
            raise mapped from error

        self._log_success(spec, user, resource_id, started, replayed=False)
        # 提交后回调不能把已持久化的成功命令变成失败响应；只记录可观测性信号。
        for callback in context.post_commit_callbacks:
            try:
                callback()
            except Exception as error:
                metrics.increment("recruit_command_post_commit_failure", command=spec.action)
                log_event(
                    logger, logging.ERROR, "command_post_commit_callback_failed",
                    command=spec.action, resource_id=resource_id,
                    error_type=type(error).__name__,
                )
        return response

    def _log_success(
        self, spec: CommandSpec, user: User | None, resource_id: str, started: float, *, replayed: bool,
    ) -> None:
        metrics.increment("recruit_command_success", command=spec.action, replayed=replayed)
        log_event(
            logger, logging.INFO, "command_succeeded",
            command=spec.action, resource_type=spec.resource_type, resource_id=resource_id,
            actor_id=user.user_id if user else "anonymous", replayed=replayed,
            duration_ms=round((perf_counter() - started) * 1000, 2),
        )

    def _rollback_and_compensate(self, context: CommandContext | None) -> None:
        """先撤销数据库，再逆序清理已登记的可逆外部资源。"""
        try:
            self.db.rollback()
        except Exception as error:
            log_event(logger, logging.ERROR, "command_rollback_failed", error_type=type(error).__name__)
        if context is None:
            return
        for compensation in reversed(context.compensations):
            try:
                compensation()
            except Exception as error:
                # 补偿失败不能遮蔽原始错误；残留资源交给对象存储巡检与告警处理。
                log_event(logger, logging.ERROR, "command_compensation_failed", error_type=type(error).__name__)

    @staticmethod
    def _validate_authentication_mode(spec: CommandSpec, user: User | None) -> None:
        if spec.authentication_mode == "anonymous_authentication":
            if spec.resource_type != "system" or not spec.audit_exempt or spec.idempotent:
                raise CommandError(
                    "command_internal_failed", "匿名命令合同配置不合法。", status_code=500,
                    retryable=True, action="retry",
                )
            return
        if user is None:
            raise CommandError(
                "command_rejected", "登录状态已失效，请重新登录。", status_code=401,
                action="none",
            )

    def _load_resource(
        self, spec: CommandSpec, resource_id: str, resource_loader: ResourceLoader | None,
    ) -> Any:
        """加载资源；Application 提供默认行锁，其余资源必须显式声明加载器。"""
        if resource_loader is not None:
            resource = resource_loader(self.db, resource_id)
        elif spec.resource_type == "application":
            resource = self.db.query(Application).filter(
                Application.application_id == resource_id,
                Application.deleted_at.is_(None),
            ).with_for_update().one_or_none()
        elif spec.resource_type in {"system", "upload"}:
            return None
        else:
            raise RuntimeError(f"command_resource_loader_required:{spec.resource_type}")
        if resource is None:
            raise BusinessError("command_resource_not_found", "未找到命令目标资源", status_code=404)
        return resource

    def _authorize(
        self,
        spec: CommandSpec,
        user: User | None,
        resource: Any,
        resource_id: str,
    ) -> None:
        """先完成授权再检查幂等重放，避免已撤权用户读取旧响应。"""
        if spec.authentication_mode == "anonymous_authentication":
            return
        if user is None:  # 防御性分支；正常情况下由 _validate_authentication_mode 拦截。
            raise CommandError("command_rejected", "登录状态已失效，请重新登录。", status_code=401)
        if spec.authorization_mode == "authenticated_self":
            # 个人游标等操作仍走统一事务边界，但绝不能伪装成没有权限码的
            # 业务命令。这里将它严格限制为当前账号自身的 system 轻量状态。
            if (
                spec.resource_type != "system"
                or spec.permission_code is not None
                or spec.requires_system_admin
                or spec.idempotent
                or not spec.audit_exempt
            ):
                raise RuntimeError("command_authenticated_self_contract_invalid")
            if resource_id != user.user_id:
                raise BusinessError(
                    "self_command_forbidden",
                    "只能修改当前账号自己的状态",
                    status_code=403,
                )
            if not self.authorization.access_context(user).is_active:
                raise BusinessError(
                    "account_inactive",
                    "当前账号已停用",
                    status_code=403,
                )
            return
        if spec.authorization_mode != "business_permission":
            raise RuntimeError("command_authorization_mode_unknown")
        if spec.requires_system_admin:
            if spec.permission_code is not None:
                raise RuntimeError("command_system_admin_permission_must_be_empty")
            self.authorization.require_system_admin(user)
            return
        if spec.resource_type == "application":
            authorization_action = spec.authorization_action or spec.action
            requirement = APPLICATION_ACTION_REQUIREMENTS.get(authorization_action)
            if requirement is None:
                raise RuntimeError(f"command_application_action_not_defined:{authorization_action}")
            # 纯失败恢复的 permission_code 为 None；不能用 dict.get() 将其
            # 误判成未登记动作，也不能要求调用方伪造一个查看职责权限。
            if requirement.permission_code != spec.permission_code:
                raise RuntimeError(f"command_permission_mismatch:{authorization_action}")
            self.authorization.require_application_action(user, resource, authorization_action)
            return
        if spec.resource_type == "resume_submission":
            authorization_action = spec.authorization_action
            if not authorization_action:
                raise RuntimeError("command_resume_submission_action_required")
            requirement = RESUME_SUBMISSION_ACTION_REQUIREMENTS.get(authorization_action)
            if requirement is None:
                raise RuntimeError(
                    f"command_resume_submission_action_not_defined:{authorization_action}"
                )
            if requirement.permission_code != spec.permission_code:
                raise RuntimeError(
                    f"command_permission_mismatch:{authorization_action}"
                )
            self.authorization.require_resume_submission_action(
                user, resource, authorization_action,
            )
            return
        if spec.permission_code is None:
            raise RuntimeError("command_permission_required")
        department_id = getattr(resource, "department_id", None) if resource is not None else None
        self.authorization.require_business_action(
            user, spec.permission_code, department_id=department_id,
            require_organization_scope=spec.require_organization_scope,
        )

    @staticmethod
    def _map_error(error: Exception) -> CommandError:
        """把领域/数据库异常投影为稳定的同步命令错误合同。"""
        if isinstance(error, CommandError):
            return error
        if isinstance(error, BusinessError):
            if error.status_code == 422 or error.code.startswith("idempotency_key_"):
                return CommandError(
                    "command_validation_failed", error.message, status_code=422,
                    action="none", context={"businessCode": error.code},
                )
            if error.status_code == 409:
                return CommandError(
                    "command_conflict", error.message, status_code=409,
                    action="refresh", context={"businessCode": error.code},
                )
            return CommandError(
                "command_rejected", error.message, status_code=error.status_code,
                action="none", context={"businessCode": error.code},
            )
        if isinstance(error, (TimeoutError, ConnectionError, OSError)):
            return CommandError(
                "command_transient_failed", "系统暂时不可用，请稍后重试。", status_code=503,
                retryable=True, action="retry",
            )
        if isinstance(error, OperationalError):
            code = getattr(getattr(error, "orig", None), "pgcode", None)
            if getattr(error, "connection_invalidated", False) or code in {"40P01", "40001"}:
                return CommandError(
                    "command_transient_failed", "系统暂时不可用，请稍后重试。", status_code=503,
                    retryable=True, action="retry",
                )
        if isinstance(error, (IntegrityError, DBAPIError)):
            return CommandError(
                "command_persistence_failed", "操作未保存，系统暂时无法写入数据。", status_code=500,
                retryable=True, action="retry",
            )
        return command_error_from_exception(error)
