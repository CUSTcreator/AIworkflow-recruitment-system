"""岗位草稿状态机：只校验人工确认前后的草稿生命周期。"""
from __future__ import annotations

from enum import StrEnum

from backend.app.shared.errors import BusinessRuleError


class JobDraftStatus(StrEnum):
    DRAFT = "draft"
    REVIEW_REQUIRED = "review_required"
    CONFIRMED = "confirmed"
    DISCARDED = "discarded"
    SKIPPED = "skipped"
    DELETED = "deleted"


class JobDraftAction(StrEnum):
    REQUIRE_REVIEW = "require_review"
    CONFIRM = "confirm"
    DISCARD = "discard"
    SKIP = "skip"
    DELETE = "delete"


_TRANSITIONS: dict[tuple[str, str], str] = {
    (JobDraftStatus.DRAFT.value, JobDraftAction.REQUIRE_REVIEW.value): JobDraftStatus.REVIEW_REQUIRED.value,
    (JobDraftStatus.DRAFT.value, JobDraftAction.CONFIRM.value): JobDraftStatus.CONFIRMED.value,
    (JobDraftStatus.DRAFT.value, JobDraftAction.DISCARD.value): JobDraftStatus.DISCARDED.value,
    (JobDraftStatus.REVIEW_REQUIRED.value, JobDraftAction.CONFIRM.value): JobDraftStatus.CONFIRMED.value,
    (JobDraftStatus.REVIEW_REQUIRED.value, JobDraftAction.DISCARD.value): JobDraftStatus.DISCARDED.value,
    (JobDraftStatus.DRAFT.value, JobDraftAction.SKIP.value): JobDraftStatus.SKIPPED.value,
    (JobDraftStatus.REVIEW_REQUIRED.value, JobDraftAction.SKIP.value): JobDraftStatus.SKIPPED.value,
    (JobDraftStatus.DRAFT.value, JobDraftAction.DELETE.value): JobDraftStatus.DELETED.value,
    (JobDraftStatus.REVIEW_REQUIRED.value, JobDraftAction.DELETE.value): JobDraftStatus.DELETED.value,
}


def transition_job_draft(draft, action: JobDraftAction | str) -> str:
    """校验并写入草稿状态；确认和丢弃均为终态。"""
    current = str(draft.status or JobDraftStatus.DRAFT.value)
    action_value = str(action)
    target = _TRANSITIONS.get((current, action_value))
    if target is None:
        raise BusinessRuleError(
            status_code=409,
            code="invalid_job_draft_transition",
            detail=f"岗位草稿不允许从 {current} 执行动作 {action_value}",
        )
    draft.status = target
    return target