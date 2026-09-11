"""一面题单页面的恢复动作投影。

Application 只保存稳定恢复码；本模块结合当前用户权限生成可执行 DTO。前端不得
根据 Workflow 状态或异常正文自行猜测重试、人工继续等按钮。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.models.entities import Application, User
from backend.app.modules.auth.public import AuthorizationService
from backend.app.shared.recovery_actions import RecoveryActionSpec, public_recovery_actions


FIRST_INTERVIEW_RECOVERY_ACTIONS: dict[str, RecoveryActionSpec] = {
    "run_first_interview_planning": RecoveryActionSpec(
        "run_first_interview_planning", "重新生成题单", retry_scope="first_interview_planning",
    ),
    "continue_first_interview_manually": RecoveryActionSpec(
        "continue_first_interview_manually", "以人工题纲继续",
        requires_input=True, retry_scope="first_interview_manual_plan",
    ),
    "review_screening_result": RecoveryActionSpec(
        "review_screening_result", "检查初步筛选结果", requires_input=True,
        retry_scope="screening_review",
    ),
    "review_interview_targets": RecoveryActionSpec(
        "review_interview_targets", "检查待核验目标", requires_input=True,
        retry_scope="screening_review",
    ),
}


def first_interview_recovery_actions(
    db: Session,
    user: User,
    application: Application,
    action_codes: list[str],
    *,
    recovery_code: str = "",
) -> list[dict[str, object]]:
    """只返回当前用户实际可执行的恢复动作。"""

    authorization = AuthorizationService(db)
    allowed: list[str] = []
    for code in action_codes:
        permission_action = (
            "run_first_interview_planning"
            if code in {"review_screening_result", "review_interview_targets"}
            else code
        )
        if authorization.can_application_action(user, application, permission_action):
            allowed.append(code)
    actions = public_recovery_actions(allowed, FIRST_INTERVIEW_RECOVERY_ACTIONS)
    if recovery_code == "first_interview_publish_retryable":
        for action in actions:
            if action["action"] == "run_first_interview_planning":
                action["label"] = "重新发布题单草稿"
                action["retryScope"] = "publish_first_interview_plan"
    return actions
