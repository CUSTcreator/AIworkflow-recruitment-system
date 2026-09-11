"""Candidate 模块的跨模块只读契约与事件订阅入口。

这里的 DTO 只携带稳定标识、版本和哈希，绝不向调用方泄漏可变 ORM 对象。
调用方可在同一短事务内读取快照或唤醒分发任务，但不得通过本模块之外的路径改写
Candidate / ResumeSubmission 的业务状态。
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from backend.app.models.entities import Candidate, ResumeProfileRecord, ResumeSubmission, SourceDocument
from backend.app.modules.candidates.domain.resume_submission_state_machine import ResumeSubmissionStatus
from backend.app.shared.errors import BusinessError


@dataclass(frozen=True)
class PublishedResumeSnapshot:
    """可供岗位分发和 Application 创建使用的一版已发布简历事实。"""

    candidate_id: str
    resume_submission_id: str
    resume_profile_id: str
    source_document_id: str
    source_sha256: str
    intake_mode: str


@dataclass(frozen=True)
class CandidateResumeRebuildView:
    """Application 读模型可读取的简历重建辅助状态，不是招聘主状态。"""

    candidate_id: str
    resume_submission_id: str | None
    status: str
    message: str | None


def get_published_resume_snapshot(db: Session, *, candidate_id: str) -> PublishedResumeSnapshot:
    """读取 Candidate 当前已发布简历；未发布时拒绝供下游创建 Application。"""
    candidate = db.get(Candidate, candidate_id)
    if candidate is None:
        raise BusinessError("candidate_not_found", "候选人不存在", status_code=404)
    if not candidate.current_resume_submission_id or not candidate.current_resume_profile_id:
        raise BusinessError("candidate_resume_not_published", "候选人当前没有已发布简历", status_code=409)
    submission = db.get(ResumeSubmission, candidate.current_resume_submission_id)
    profile = db.get(ResumeProfileRecord, candidate.current_resume_profile_id)
    if submission is None or profile is None:
        raise BusinessError("candidate_resume_reference_missing", "候选人当前简历引用不完整", status_code=409)
    if submission.candidate_id != candidate.candidate_id or profile.candidate_id != candidate.candidate_id:
        raise BusinessError("candidate_resume_reference_mismatch", "候选人当前简历引用不一致", status_code=409)
    if str(submission.status) != ResumeSubmissionStatus.COMPLETED.value:
        raise BusinessError("candidate_resume_not_completed", "候选人简历尚未完成处理", status_code=409)
    document = db.get(SourceDocument, submission.source_document_id)
    if document is None:
        raise BusinessError("candidate_resume_document_missing", "候选人简历原文件不存在", status_code=409)
    return PublishedResumeSnapshot(
        candidate_id=candidate.candidate_id,
        resume_submission_id=submission.resume_submission_id,
        resume_profile_id=profile.resume_profile_id,
        source_document_id=document.source_document_id,
        source_sha256=document.source_sha256,
        intake_mode=str(submission.intake_mode),
    )


def get_candidate_resume_rebuild_view(db: Session, *, candidate_id: str) -> CandidateResumeRebuildView:
    """只读投影：供 Application 页面显示“招聘阶段 · 简历重建状态”。"""
    candidate = db.get(Candidate, candidate_id)
    if candidate is None or not candidate.current_resume_submission_id:
        return CandidateResumeRebuildView(candidate_id=candidate_id, resume_submission_id=None, status="idle", message=None)
    submission = db.get(ResumeSubmission, candidate.current_resume_submission_id)
    if submission is None or str(submission.intake_mode) == "initial":
        return CandidateResumeRebuildView(candidate_id=candidate_id, resume_submission_id=candidate.current_resume_submission_id, status="idle", message=None)
    status = str(submission.status)
    if status in {"queued", "parsing", "extracting"}:
        return CandidateResumeRebuildView(candidate_id=candidate_id, resume_submission_id=submission.resume_submission_id, status="processing", message="简历重建处理中")
    if status == "review_required":
        return CandidateResumeRebuildView(candidate_id=candidate_id, resume_submission_id=submission.resume_submission_id, status="review_required", message=submission.error_message or "简历重建待确认")
    if status == "failed":
        return CandidateResumeRebuildView(candidate_id=candidate_id, resume_submission_id=submission.resume_submission_id, status="failed", message=submission.error_message or "简历重建失败")
    return CandidateResumeRebuildView(candidate_id=candidate_id, resume_submission_id=submission.resume_submission_id, status="completed", message="简历已更新")
