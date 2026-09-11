"""岗位申请主流程的统一业务状态入口。

本服务接收 HTTP 命令或 Workflow 发布步骤已经得到的业务动作，调用
``Application`` 状态机校验后更新申请主状态。它不提交事务、不访问 HTTP 请求、
不执行外部调用；调用方必须在自己的短事务中调用它。

简历解析、简历重建等 Candidate 子流程不能写入这里的主状态。招聘流程页需要展示
的“简历重建中”等信息，应由 Application 读模型关联 Candidate 的当前简历任务派生。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from backend.app.modules.applications.domain.application_state_machine import (
    INITIAL_STATUS,
    InvalidTransition,
    next_status,
)
from backend.app.shared.errors import BusinessError


@dataclass(frozen=True)
class ApplicationTransition:
    """一次已校验的招聘主流程状态变化，供审计与事件记录复用。"""

    action: str
    from_status: str
    to_status: str


class ApplicationProcessService:
    """Application 招聘主流程唯一的状态解释入口。"""

    @staticmethod
    def initial_status() -> str:
        """返回新申请的唯一主流程入口；创建后必须先进入硬筛。"""
        return INITIAL_STATUS

    @staticmethod
    def plan_transition(application, *, action: str) -> ApplicationTransition:
        """只校验并生成状态变化计划，不提前改写对象。"""
        from_status = str(application.status or "")
        try:
            to_status = next_status(from_status, action)
        except InvalidTransition as error:
            raise BusinessError(
                "application_transition_invalid",
                f"当前招聘阶段不允许执行该操作：{error}",
                status_code=409,
            ) from error
        return ApplicationTransition(action=action, from_status=from_status, to_status=to_status)

    @staticmethod
    def apply_transition(application, transition: ApplicationTransition, *, now: datetime, owner: str | None = None) -> None:
        """应用已校验计划；避免审计、任务处理与状态写入出现两套判断。"""
        if str(application.status or "") != transition.from_status:
            raise BusinessError(
                "application_status_changed",
                "申请状态已变化，请刷新后重试",
                status_code=409,
            )
        application.status = transition.to_status
        application.updated_at = now
        if owner is not None:
            application.current_owner = owner

    @staticmethod
    def transition(application, *, action: str, now: datetime, owner: str | None = None) -> ApplicationTransition:
        """适用于无需额外后置操作的直接状态迁移。"""
        transition = ApplicationProcessService.plan_transition(application, action=action)
        ApplicationProcessService.apply_transition(application, transition, now=now, owner=owner)
        return transition

    @staticmethod
    def ensure_status(application, expected_status: str) -> None:
        """供 Workflow 发布前核验其运行时输入仍对应预期招聘阶段。"""
        if str(application.status or "") != expected_status:
            raise BusinessError(
                "application_status_mismatch",
                f"申请当前状态为 {application.status}，不能发布要求 {expected_status} 的结果",
                status_code=409,
            )