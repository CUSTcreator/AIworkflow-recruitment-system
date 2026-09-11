"""候选人简历管理领域规则。

本包只保存不依赖数据库、HTTP 或队列的状态与业务规则；
实际落库和下游工作流入队由上层服务负责。
"""

from .candidate_lifecycle_state_machine import CandidateLifecycleStatus
from .resume_submission_state_machine import (
    ResumeFailureKind,
    ResumeIntakeMode,
    ResumeReviewKind,
    ResumeSubmissionStatus,
    transition_submission_status,
)

__all__ = [
    "CandidateLifecycleStatus",
    "ResumeFailureKind",
    "ResumeIntakeMode",
    "ResumeReviewKind",
    "ResumeSubmissionStatus",
    "transition_submission_status",
]
