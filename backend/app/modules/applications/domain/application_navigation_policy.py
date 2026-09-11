"""招聘流程页面导航规则：根据申请状态计算前端主入口。"""


_WORKSPACE_ACTIONS: dict[str, tuple[str, str]] = {
    "department_review": (
        "open_screening_workspace", "查看初步筛选审核",
    ),
    "first_interview_planning": (
        "open_first_interview_plan", "查看一面题单",
    ),
    "first_interview_scheduled": (
        "open_first_interview_workspace", "查看一面工作台",
    ),
    "first_interview_in_progress": (
        "open_first_interview_workspace", "查看一面工作台",
    ),
    "first_interview_evaluation": (
        "open_first_interview_workspace", "查看一面工作台",
    ),
    "hr_second_review": (
        "open_post_first_review", "查看一面后评估",
    ),
    "second_interview_in_progress": (
        "open_second_interview_workspace", "查看二面工作台",
    ),
    "second_interview_evaluation": (
        "open_second_interview_workspace", "查看二面工作台",
    ),
    "final_review": (
        "open_final_review", "查看二面后评估",
    ),
}


def application_main_route(
    application_id: str,
    status: str,
    rejection_stage: str | None = None,
) -> str:
    """返回当前申请状态对应的前端主页面路径。"""
    if status == "closed_rejected":
        if rejection_stage == "hard_screening":
            return "/candidates"
        return {
            "screening": f"/applications/{application_id}/screening-review",
            "first_interview": f"/applications/{application_id}/interviews/first/workspace",
            "hr_review": f"/applications/{application_id}/hr-second-review",
            "second_interview": f"/applications/{application_id}/interviews/second/workspace",
            "final_review": f"/applications/{application_id}/final-review",
        }.get(rejection_stage, f"/applications/{application_id}/final-review")
    if status in {
        "waiting_job_profile",
        "hard_screening_pending",
        "hard_screening_running",
        "hard_screening_review",
        "screening_failed",
        "on_hold",
        "manual_review",
    }:
        return "/candidates"
    if status in {"submitted", "screening_running", "department_review"}:
        # 初筛 V1 保留完整的审核工作台：其中包含简历原文、证据定位和推进/不推进操作。
        # 通用评估页仅可作为后续版本的独立展示能力，不能替换 V1 的默认业务入口。
        return f"/applications/{application_id}/screening-review"
    if status == "first_interview_planning":
        return f"/applications/{application_id}/interviews/first/plan"
    if status in {"first_interview_scheduled", "first_interview_in_progress", "first_interview_evaluation"}:
        # first_interview_scheduled 仅兼容历史数据；新申请在确认题单后会直接进入 in_progress。
        return f"/applications/{application_id}/interviews/first/workspace"
    if status == "hr_second_review":
        return f"/applications/{application_id}/hr-second-review"
    if status in {"second_interview_in_progress", "second_interview_evaluation"}:
        return f"/applications/{application_id}/interviews/second/workspace"
    if status in {"final_review", "offer_process", "closed_cancelled", "resume_replaced"}:
        return f"/applications/{application_id}/final-review"
    return "/candidates"


def application_primary_action(
    application_id: str,
    status: str,
    rejection_stage: str | None = None,
) -> dict[str, str]:
    """返回列表摘要的主操作；硬筛结果是弹窗动作，不是页面导航。"""
    if status == "closed_rejected" and rejection_stage == "hard_screening":
        return {
            "type": "view_hard_screening_result",
            "label": "查看硬筛结果",
        }
    return {
        "type": "navigate",
        "label": "查看流程",
        "route": application_main_route(application_id, status, rejection_stage),
    }


def application_workspace_action(
    application_id: str,
    status: str,
    rejection_stage: str | None = None,
) -> dict[str, str] | None:
    """返回列表中唯一的工作台导航动作。

    工作台入口属于读取型导航，不和 ``availableActions`` 中的写操作混用。
    页面是否展示只由这个后端投影决定，避免前端根据状态或本地权限自行猜测。
    """
    spec = _WORKSPACE_ACTIONS.get(status)
    if spec is None:
        return None
    action, label = spec
    return {
        "action": action,
        "label": label,
        "route": application_main_route(application_id, status, rejection_stage),
    }
