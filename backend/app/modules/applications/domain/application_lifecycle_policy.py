"""Application 生命周期策略：判断替换和招聘流程是否允许继续。"""
from __future__ import annotations

from backend.app.models.entities import Application


TERMINAL_APPLICATION_STATUSES = frozenset({
    "department_review", "offer_process", "closed_rejected",
    "closed_cancelled", "resume_replaced",
})

# 招聘流程已经结束的状态。与上面的历史兼容集合分开维护：
# ``department_review`` 仍是业务页面可操作阶段，不能因为旧的生命周期
# 判断集合包含它，就把页面上的后续执行轨迹隐藏掉。
CLOSED_APPLICATION_STATUSES = frozenset({
    "offer_process", "closed_rejected", "closed_cancelled", "resume_replaced",
})


def is_application_closed(status: str | None) -> bool:
    """返回申请是否已经进入不可继续招聘的业务终态。"""
    return str(status or "") in CLOSED_APPLICATION_STATUSES


def is_recruitment_in_progress(status: str) -> bool:
    return status not in TERMINAL_APPLICATION_STATUSES


def can_replace_application(application: Application) -> bool:
    return application.status == "department_review"

