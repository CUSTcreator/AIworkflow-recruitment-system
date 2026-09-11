"""Candidate 档案生命周期状态机。

Candidate 表示候选人这一长期业务主体，而不是某一份 PDF 的处理任务。
因此本状态机刻意只保留档案是否有效的状态；解析、待确认、分发、
失败等过程状态由 ResumeSubmission 和 WorkflowRun 分别承担。
"""
from __future__ import annotations

from enum import StrEnum

from backend.app.shared.errors import BusinessRuleError


class CandidateLifecycleStatus(StrEnum):
    """候选人档案仅有的生命周期状态。"""

    ACTIVE = "active"
    ARCHIVED = "archived"


_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    CandidateLifecycleStatus.ACTIVE: {CandidateLifecycleStatus.ARCHIVED},
    CandidateLifecycleStatus.ARCHIVED: {CandidateLifecycleStatus.ACTIVE},
}


def transition_candidate_lifecycle(candidate, target: CandidateLifecycleStatus | str) -> None:
    """校验并变更 Candidate 的长期生命周期，不承载简历处理状态。"""
    current = str(candidate.status or CandidateLifecycleStatus.ACTIVE.value)
    target_value = str(target)
    if current == target_value:
        return
    if current not in _ALLOWED_TRANSITIONS or target_value not in _ALLOWED_TRANSITIONS[current]:
        raise BusinessRuleError(
            status_code=409,
            code="invalid_candidate_lifecycle_transition",
            detail=f"不允许候选人档案从 {current} 变更为 {target_value}",
        )
    candidate.status = target_value
