"""ResumeSubmission 的状态与原因契约。

一条 ResumeSubmission 只描述一版 PDF 的解析、结构化与人工确认过程。
岗位分发、初筛和面试均不应改写该状态。状态机本身保持纯函数性质：
不访问数据库、不提交事务、不调用外部服务，也不负责入队下游 Workflow。
"""
from __future__ import annotations

from enum import StrEnum

from backend.app.shared.errors import BusinessRuleError


class ResumeSubmissionStatus(StrEnum):
    """单次简历提交的处理状态。"""

    QUEUED = "queued"
    PARSING = "parsing"
    EXTRACTING = "extracting"
    REVIEW_REQUIRED = "review_required"
    FAILED = "failed"
    COMPLETED = "completed"


class ResumeIntakeMode(StrEnum):
    """本次简历版本相对 Candidate 的来源。"""

    INITIAL = "initial"
    REPARSE = "reparse"
    REPLACEMENT = "replacement"
    MANUAL_CORRECTION = "manual_correction"


class ResumeReviewKind(StrEnum):
    """简历任务允许用户在页面上处理的明确人工确认原因。"""

    DUPLICATE_MATCH = "duplicate_match"
    DUPLICATE_AMBIGUOUS = "duplicate_ambiguous"
    DUPLICATE_BLOCKED = "duplicate_blocked"
    STRUCTURE_METADATA = "structure_metadata"
    STRUCTURE_OUTLINE = "structure_outline"


class ResumeFailureKind(StrEnum):
    """终态失败的归类，供 API 与前端提供正确的恢复动作。"""

    INVALID_SOURCE_FILE = "invalid_source_file"
    PARSE_FAILED = "parse_failed"
    STRUCTURE_FAILED = "structure_failed"
    EXTERNAL_SERVICE_FAILED = "external_service_failed"
    WORKFLOW_TIMEOUT = "workflow_timeout"


class ResumeRecoveryCode(StrEnum):
    """用户可理解且可执行的简历恢复原因。

    ``failure_kind`` 只说明失败的大类；本枚举说明页面应当提供哪一种恢复
    操作。原始供应商异常和调用栈仍只保存在 Workflow 检查点与日志中。
    """

    SOURCE_UNAVAILABLE = "source_unavailable"
    INVALID_SOURCE_FILE = "invalid_source_file"
    PARSE_QUALITY_REJECTED = "parse_quality_rejected"
    PARSE_RETRY_EXHAUSTED = "parse_retry_exhausted"
    STRUCTURE_INPUT_INSUFFICIENT = "structure_input_insufficient"
    STRUCTURE_FAILED = "structure_failed"
    PUBLISH_RETRYABLE = "publish_retryable"
    ROUTING_RETRYABLE = "routing_retryable"
    ROUTING_MANUAL_SELECTION = "routing_manual_selection"
    DUPLICATE_BLOCKED = "duplicate_blocked"


RESUME_SUBMISSION_STATUS_VALUES = tuple(item.value for item in ResumeSubmissionStatus)
RESUME_INTAKE_MODE_VALUES = tuple(item.value for item in ResumeIntakeMode)

# 人工归属决策可以完成本次提交；历史结构化技术待确认允许收敛为失败。
_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    ResumeSubmissionStatus.QUEUED: {ResumeSubmissionStatus.PARSING, ResumeSubmissionStatus.FAILED},
    ResumeSubmissionStatus.PARSING: {ResumeSubmissionStatus.EXTRACTING, ResumeSubmissionStatus.FAILED},
    ResumeSubmissionStatus.EXTRACTING: {ResumeSubmissionStatus.REVIEW_REQUIRED, ResumeSubmissionStatus.COMPLETED, ResumeSubmissionStatus.FAILED},
    ResumeSubmissionStatus.REVIEW_REQUIRED: {ResumeSubmissionStatus.COMPLETED, ResumeSubmissionStatus.FAILED},
    # 失败任务不可原地复活；重试必须新建 reparse Submission。
    ResumeSubmissionStatus.FAILED: set(),
    ResumeSubmissionStatus.COMPLETED: set(),
}


def transition_submission_status(submission, target: ResumeSubmissionStatus | str) -> None:
    """校验并更新一次简历处理任务的状态。

    ``completed`` 只表示当前 PDF 已成功发布为 ResumeProfile；后续岗位分发
    无命中、等待或失败都不能将它倒退为 ``review_required``。重新解析、重新
    上传必须创建新 Submission，以保留每一版简历的审计事实。
    """
    current = str(submission.status or "")
    target_value = str(target)
    if current == target_value:
        return
    if current not in _ALLOWED_TRANSITIONS:
        raise BusinessRuleError(status_code=409, code="invalid_resume_submission_status", detail=f"简历任务当前状态无效：{current}")
    if target_value not in _ALLOWED_TRANSITIONS[current]:
        raise BusinessRuleError(status_code=409, code="invalid_resume_submission_transition", detail=f"不允许简历任务从 {current} 变更为 {target_value}")
    submission.status = target_value


def is_review_required(submission) -> bool:
    """判断当前 Submission 是否等待用户确认。"""
    return str(submission.status) == ResumeSubmissionStatus.REVIEW_REQUIRED.value
