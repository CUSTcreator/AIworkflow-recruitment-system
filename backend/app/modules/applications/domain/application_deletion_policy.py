"""Application 删除策略：定义候选人删除时的状态约束。"""
from __future__ import annotations


DELETION_BLOCKING_STATUSES = frozenset({
    "first_interview_planning",
    "first_interview_scheduled",
    "first_interview_in_progress",
    "first_interview_evaluation",
    "hr_second_review",
    "second_interview_in_progress",
    "second_interview_evaluation",
    "final_review",
})

EARLY_CANCELLABLE_STATUSES = frozenset({
    "waiting_job_profile",
    "hard_screening_pending",
    "hard_screening_running",
    "hard_screening_review",
    "submitted",
    "screening_running",
    "screening_failed",
    "department_review",
})

REVIEW_HOLD_STATUSES = frozenset({"on_hold", "manual_review"})


def blocks_owner_deletion(status: str, *, source_status: str | None = None) -> bool:
    if status in DELETION_BLOCKING_STATUSES:
        return True
    # 暂缓/人工复核继承其进入该状态前所在的招聘阶段；无法确认时按阻断处理。
    return status in REVIEW_HOLD_STATUSES and source_status not in EARLY_CANCELLABLE_STATUSES


def can_cancel_for_owner_deletion(status: str, *, source_status: str | None = None) -> bool:
    return status in EARLY_CANCELLABLE_STATUSES or (
        status in REVIEW_HOLD_STATUSES and source_status in EARLY_CANCELLABLE_STATUSES
    )
