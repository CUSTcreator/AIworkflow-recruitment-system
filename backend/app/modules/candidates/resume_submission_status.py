"""兼容入口：ResumeSubmission 状态规则已迁入 ``candidates.domain``。

历史模块仍可从此处导入，避免一次重构影响全部调用方；新增代码必须直接导入
``backend.app.modules.candidates.domain.resume_submission_state_machine``。
"""
from backend.app.modules.candidates.domain.resume_submission_state_machine import (
    RESUME_INTAKE_MODE_VALUES,
    RESUME_SUBMISSION_STATUS_VALUES,
    ResumeFailureKind,
    ResumeIntakeMode,
    ResumeReviewKind,
    ResumeSubmissionStatus,
    is_review_required,
    transition_submission_status,
)

__all__ = [
    "RESUME_INTAKE_MODE_VALUES",
    "RESUME_SUBMISSION_STATUS_VALUES",
    "ResumeFailureKind",
    "ResumeIntakeMode",
    "ResumeReviewKind",
    "ResumeSubmissionStatus",
    "is_review_required",
    "transition_submission_status",
]