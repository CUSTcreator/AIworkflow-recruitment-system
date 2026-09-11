"""候选人简历管理的流程决策器。

本服务是 Candidate/ResumeSubmission 子流程唯一的状态解释入口。它接收用户
命令或 Workflow 阶段结果，调用领域状态机完成状态变更，并写入供读模型使用的
明确结果。它不提交事务、不读取 HTTP 请求，也不执行外部服务；调用方必须在
各自的短事务或 StepRunner 的 ``persist_success`` 中调用本服务。
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from backend.app.modules.candidates.domain.candidate_lifecycle_state_machine import (
    CandidateLifecycleStatus,
    transition_candidate_lifecycle,
)
from backend.app.modules.candidates.domain.resume_submission_state_machine import (
    ResumeFailureKind,
    ResumeReviewKind,
    ResumeSubmissionStatus,
    transition_submission_status,
)


class RoutingOutcomeStatus(StrEnum):
    """岗位分发的业务结果，不属于 Candidate 或 ResumeSubmission 状态。"""

    PROCESSING = "processing"
    AUTO_MATCHED = "auto_matched"
    MANUAL_SELECTED = "manual_selected"
    MANUAL_SELECTION_AVAILABLE = "manual_selection_available"
    WAITING_FOR_JOB_PROFILES = "waiting_for_job_profiles"
    FAILED = "failed"


class CandidateIntakeProcessService:
    """
    集中处理 ResumeSubmission 状态、确认原因与岗位分发结果。

    ``SourceDocument`` 只描述上传文件本身是否可用；解析、结构化、发布和分发等
    每次处理尝试的状态属于 ``ResumeSubmission``，不得再同步回文件状态。

    具体流程为
    HTTP 用户操作 / Worker 步骤完成 / 岗位画像完成
                ↓
    CandidateIntakeProcessService
    读取 Candidate + 当前 ResumeSubmission + Workflow 产物
                ↓
    状态机校验
        是否允许 queued → parsing
        是否允许 review_required → completed / failed（历史技术待确认收敛）
        failed / completed 等终态只能通过新建子 Submission 重试，禁止原地回退
                ↓
    流程决策
     重复候选人？
     入队岗位分发？
     人工选择岗位？
     重评分？
                ↓
    短事务执行等具体操作
    """

    @staticmethod
    def activate_candidate(candidate, *, now: datetime) -> None:
        """将历史过程态 Candidate 收敛为有效档案，归档档案不得被隐式恢复。"""
        if candidate is not None and str(candidate.status) != CandidateLifecycleStatus.ARCHIVED.value:
            transition_candidate_lifecycle(candidate, CandidateLifecycleStatus.ACTIVE)
            candidate.updated_at = now

    @staticmethod
    def begin_parsing(submission, *, document=None, candidate=None, now: datetime) -> None:
        """发布“开始解析”事件。

        Workflow 不能自行修改 ``ResumeSubmission.status``；它只能在 StepRunner
        已确认本步骤可以发布时调用此方法。这样 HTTP 重试、Worker 恢复与首次执行
        使用完全一致的状态约束。
        """
        transition_submission_status(submission, ResumeSubmissionStatus.PARSING)
        submission.review_kind = None
        submission.failure_kind = None
        submission.recovery_code = None
        submission.recovery_context_json = {}
        submission.error_message = None
        submission.updated_at = now
        CandidateIntakeProcessService.activate_candidate(candidate, now=now)

    @staticmethod
    def begin_extracting(submission, *, document=None, candidate=None, now: datetime) -> None:
        """发布“文本解析完成，开始结构化”事件。"""
        transition_submission_status(submission, ResumeSubmissionStatus.EXTRACTING)
        submission.error_message = None
        submission.updated_at = now
        CandidateIntakeProcessService.activate_candidate(candidate, now=now)
    @staticmethod
    def require_review(
        submission,
        *,
        review_kind: ResumeReviewKind | str,
        reason: str,
        document=None,
        candidate=None,
        now: datetime,
        recovery_code: str | None = None,
        recovery_context: dict[str, Any] | None = None,
    ) -> None:
        """发布明确类型的人工任务，不把技术失败伪装成待确认。"""
        transition_submission_status(submission, ResumeSubmissionStatus.REVIEW_REQUIRED)
        submission.review_kind = str(review_kind)
        submission.failure_kind = None
        submission.recovery_code = recovery_code
        submission.recovery_context_json = dict(recovery_context or {})
        submission.error_message = reason
        submission.review_context_json = {**dict(getattr(submission, "review_context_json", None) or {}), "reviewKind": str(review_kind), "reason": reason}
        submission.updated_at = now
        CandidateIntakeProcessService.activate_candidate(candidate, now=now)

    @staticmethod
    def complete_submission(submission, *, document=None, candidate=None, now: datetime) -> None:
        """标记已发布画像的版本完成；分发结果不能再影响这一简历终态。"""
        transition_submission_status(submission, ResumeSubmissionStatus.COMPLETED)
        submission.review_kind = None
        submission.failure_kind = None
        submission.recovery_code = None
        submission.recovery_context_json = {}
        submission.error_message = None
        submission.updated_at = now
        CandidateIntakeProcessService.activate_candidate(candidate, now=now)

    @staticmethod
    def fail_submission(
        submission,
        *,
        failure_kind: ResumeFailureKind | str,
        reason: str,
        document=None,
        candidate=None,
        now: datetime,
        retrying: bool = False,
        recovery_code: str | None = None,
        recovery_context: dict[str, Any] | None = None,
    ) -> None:
        """记录终态失败；当前 Step 仍会重试时不提前将 Submission 置为失败。"""
        if retrying:
            CandidateIntakeProcessService.activate_candidate(candidate, now=now)
            return
        transition_submission_status(submission, ResumeSubmissionStatus.FAILED)
        submission.review_kind = None
        submission.failure_kind = str(failure_kind)
        submission.recovery_code = recovery_code
        submission.recovery_context_json = dict(recovery_context or {})
        submission.error_message = reason[:2000]
        submission.updated_at = now
        CandidateIntakeProcessService.activate_candidate(candidate, now=now)

    @staticmethod
    def mark_routing(
        submission,
        *,
        status: RoutingOutcomeStatus | str,
        reason: str | None = None,
        now: datetime,
        extra: dict[str, Any] | None = None,
        recovery_code: str | None = None,
        recovery_context: dict[str, Any] | None = None,
    ) -> None:
        """写入分发结果投影；绝不修改 Candidate 生命周期或简历完成状态。

        分发是 Submission 的独立阶段，状态、原因和结果均使用具名字段。仅迁移期从
        旧 ``payload.majorRouting`` 读一次，任何新写入都不得回写泛化 payload。
        """
        routing = dict(getattr(submission, "routing_result_json", None) or {})
        for state_key in ("status", "reason", "recovery_code", "recovery_context"):
            routing.pop(state_key, None)
        if extra:
            routing.update(extra)
            for state_key in ("status", "reason", "recovery_code", "recovery_context"):
                routing.pop(state_key, None)
        if recovery_code:
            submission.recovery_code = recovery_code
            submission.recovery_context_json = dict(recovery_context or {})
        elif str(status) not in {
            RoutingOutcomeStatus.FAILED.value,
            RoutingOutcomeStatus.MANUAL_SELECTION_AVAILABLE.value,
        }:
            submission.recovery_code = None
            submission.recovery_context_json = {}
        submission.routing_status = str(status)
        submission.routing_reason = reason
        submission.routing_result_json = routing
        submission.updated_at = now

    @staticmethod
    def complete_with_routing(
        submission,
        *,
        routing_status: RoutingOutcomeStatus | str,
        reason: str | None = None,
        routing_extra: dict[str, Any] | None = None,
        recovery_code: str | None = None,
        recovery_context: dict[str, Any] | None = None,
        document=None,
        candidate=None,
        now: datetime,
    ) -> None:
        """原子发布“简历已完成 + 当前岗位分发结论”。

        ``ResumeSubmission`` 的完成只表示 PDF 已得到正式 ResumeProfile；岗位是否
        自动命中、等待岗位画像或允许人工选岗是另一个结果，因此写入 Submission 的
        ``routing_*`` 具名字段，而不是污染 Candidate 或 Application 主状态。
        """
        CandidateIntakeProcessService.complete_submission(
            submission, document=document, candidate=candidate, now=now
        )
        CandidateIntakeProcessService.mark_routing(
            submission,
            status=routing_status,
            reason=reason,
            extra=routing_extra,
            recovery_code=recovery_code,
            recovery_context=recovery_context,
            now=now,
        )
