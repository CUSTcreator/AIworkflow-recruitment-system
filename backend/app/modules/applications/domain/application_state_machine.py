"""Application 状态机：只定义岗位申请的招聘主流程允许迁移。

简历上传、解析、重建状态属于 Candidate/ResumeSubmission，不能写入 Application.status。"""
from __future__ import annotations


INITIAL_STATUS = "hard_screening_pending"
WAITING_JOB_PROFILE_STATUS = "waiting_job_profile"

TRANSITIONS: dict[tuple[str, str], str] = {
    # 岗位已确认但画像尚未完成时，Application 已经存在且冻结了简历/JD 版本；
    # 只能在 JobProfileReady 事件中进入后续初筛入口。
    (WAITING_JOB_PROFILE_STATUS, "job_profile_ready"): "hard_screening_pending",
    (WAITING_JOB_PROFILE_STATUS, "cancel_recruitment"): "closed_cancelled",
    ("hard_screening_pending", "run_hard_screening"): "hard_screening_running",
    ("hard_screening_pending", "skip_hard_screening"): "submitted",
    ("hard_screening_running", "complete_hard_screening_passed"): "submitted",
    ("hard_screening_running", "complete_hard_screening_failed"): "closed_rejected",
    ("hard_screening_running", "complete_hard_screening_review"): "hard_screening_review",
    ("hard_screening_pending", "fail_hard_screening"): "hard_screening_review",
    ("hard_screening_running", "fail_hard_screening"): "hard_screening_review",
    # 有硬筛处理权限的业务用户可重新入队；恢复动作仍由后端按部门范围投影。
    ("hard_screening_review", "retry_hard_screening"): "hard_screening_pending",
    ("hard_screening_running", "retry_hard_screening"): "hard_screening_pending",
    ("hard_screening_pending", "retry_hard_screening"): "hard_screening_pending",
    ("hard_screening_review", "review_hard_screening_pass"): "submitted",
    ("hard_screening_review", "review_hard_screening_reject"): "closed_rejected",
    ("submitted", "run_scoring"): "screening_running",
    ("screening_failed", "retry_scoring"): "screening_running",
    ("screening_running", "complete_scoring"): "department_review",
    ("screening_running", "fail_scoring"): "screening_failed",
    ("hard_screening_pending", "cancel_recruitment"): "closed_cancelled",
    ("hard_screening_running", "cancel_recruitment"): "closed_cancelled",
    ("hard_screening_review", "cancel_recruitment"): "closed_cancelled",
    ("submitted", "cancel_recruitment"): "closed_cancelled",
    ("screening_running", "cancel_recruitment"): "closed_cancelled",
    ("screening_failed", "cancel_recruitment"): "closed_cancelled",
    ("department_review", "cancel_recruitment"): "closed_cancelled",
    ("on_hold", "cancel_recruitment"): "closed_cancelled",
    ("manual_review", "cancel_recruitment"): "closed_cancelled",
    ("department_review", "approve_first_interview"): "first_interview_planning",
    ("department_review", "reject"): "closed_rejected",
    ("department_review", "hold"): "on_hold",
    ("department_review", "manual_review"): "manual_review",
    ("first_interview_planning", "run_first_interview_planning"): "first_interview_planning",
    # 确认题单即代表面试官已可开始记录并作出一面结论；不再保留仅用于点击
    # “开始一面”的中间页面/中间状态，避免正式题单确认后出现无业务价值的一跳。
    ("first_interview_planning", "confirm_first_guide"): "first_interview_in_progress",
    # 自动题单工作流失败后，面试官可明确转为人工题纲；失败的 WorkflowRun 保留用于排障，
    # Application 主流程仍可凭正式人工题纲进入一面。
    ("first_interview_planning", "continue_first_interview_manually"): "first_interview_in_progress",
    ("first_interview_scheduled", "start_first_interview"): "first_interview_in_progress",
    ("first_interview_in_progress", "finish_first_interview"): "first_interview_evaluation",
    ("first_interview_evaluation", "submit_first_feedback"): "hr_second_review",
    ("first_interview_in_progress", "complete_first_interview_pass"): "hr_second_review",
    ("first_interview_evaluation", "complete_first_interview_pass"): "hr_second_review",
    ("first_interview_in_progress", "complete_first_interview_reject"): "closed_rejected",
    ("first_interview_evaluation", "complete_first_interview_reject"): "closed_rejected",
    ("hr_second_review", "approve_second_interview"): "second_interview_in_progress",
    ("hr_second_review", "reject"): "closed_rejected",
    ("hr_second_review", "hold"): "on_hold",
    ("hr_second_review", "manual_review"): "manual_review",
    ("second_interview_in_progress", "finish_second_interview"): "second_interview_evaluation",
    ("second_interview_evaluation", "submit_second_feedback"): "final_review",
    ("second_interview_in_progress", "complete_second_interview_pass"): "final_review",
    ("second_interview_evaluation", "complete_second_interview_pass"): "final_review",
    ("second_interview_in_progress", "complete_second_interview_reject"): "closed_rejected",
    ("second_interview_evaluation", "complete_second_interview_reject"): "closed_rejected",
    ("final_review", "offer"): "offer_process",
    ("final_review", "reject"): "closed_rejected",
    ("final_review", "manual_review"): "manual_review",
}


class InvalidTransition(ValueError):
    pass


def next_status(current_status: str, action: str) -> str:
    try:
        return TRANSITIONS[(current_status, action)]
    except KeyError as exc:
        raise InvalidTransition(f"当前状态 {current_status} 不能执行动作 {action}") from exc

