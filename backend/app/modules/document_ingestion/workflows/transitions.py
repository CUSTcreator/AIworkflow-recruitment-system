"""文档导入 Workflow 的失败结果投影。

简历任务的失败、原因字段及 Candidate 生命周期均由 CandidateIntakeProcessService
统一处理；岗位任务投影到 JobDocumentImport，文件资产不承载流程状态。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from backend.app.models.entities import Candidate, JobDocumentImport, ResumeSubmission, SourceDocument
from backend.app.modules.candidates.public import ResumeFailureKind, ResumeReviewKind
from backend.app.modules.candidates.domain.resume_submission_state_machine import ResumeRecoveryCode
from backend.app.modules.candidates.public import CandidateIntakeProcessService
from backend.app.shared.workflows import WorkflowTransitionContext
from backend.app.modules.jobs.job_recovery import JobRecoveryCode
from backend.app.modules.document_ingestion.services.job_document_import_process_service import JobDocumentImportProcessService
from backend.app.modules.document_ingestion.parsing import ExcelDocumentParser
from backend.app.storage.object_store import ObjectStore


def transition_job_document(
    db: Any, run: Any, now: datetime, error: Exception, retrying: bool
) -> None:
    """投影岗位导入任务的失败或重试状态；它不修改文件资产。"""
    import_task = db.get(JobDocumentImport, run.subject_id) if run.subject_id else None
    if import_task is None:
        return
    code = str(getattr(error, "error_code", "") or "").casefold()
    if retrying:
        recovery_code = JobRecoveryCode.EXTRACTION_RETRYABLE.value
    elif "header" in code:
        recovery_code = JobRecoveryCode.HEADER_REVIEW_REQUIRED.value
    elif "field" in code:
        recovery_code = JobRecoveryCode.FIELD_REVIEW_REQUIRED.value
    elif "publish" in code:
        recovery_code = JobRecoveryCode.DRAFT_PUBLISH_RETRYABLE.value
    else:
        recovery_code = JobRecoveryCode.EXTRACTION_RETRYABLE.value
    JobDocumentImportProcessService.fail(
        import_task,
        failure_kind="workflow_failed",
        reason=str(error),
        recovery_code=recovery_code,
        recovery_context={
            "failedStep": str(getattr(error, "step_name", "") or "") or None,
            "errorCode": code or None,
        },
        retrying=retrying,
        now=now,
    )


def transition_resume_document(
    db: Any,
    run: Any,
    now: datetime,
    error: Exception,
    retrying: bool,
    *,
    error_code: str = "",
    error_category: str = "",
    step_name: str = "",
) -> None:
    """将简历导入 Workflow 的技术终态映射为 Submission 的失败事实。"""
    submission = db.get(ResumeSubmission, run.subject_id) if run.subject_id else None
    if submission is None:
        return
    document = db.get(SourceDocument, submission.source_document_id)
    candidate = db.get(Candidate, submission.candidate_id) if submission.candidate_id else None
    error_text = str(error)
    error_code = error_code or str(getattr(error, "error_code", "") or "")
    step_name = step_name or str(getattr(error, "step_name", "") or "")
    recovery_code = _resume_failure_recovery_code(
        error_code=error_code,
        error_text=error_text,
        step_name=step_name,
    )
    normalized_error = error_text.casefold()
    failure_kind = (
        ResumeFailureKind.WORKFLOW_TIMEOUT
        if "timeout" in normalized_error
        else ResumeFailureKind.STRUCTURE_FAILED
        if normalized_error.startswith("resume_structure_failed:")
        else ResumeFailureKind.EXTERNAL_SERVICE_FAILED
    )
    CandidateIntakeProcessService.fail_submission(
        submission,
        failure_kind=failure_kind,
        reason=error_text,
        document=document,
        candidate=candidate,
        now=now,
        retrying=retrying,
        recovery_code=recovery_code,
        recovery_context={
            "failed_step": step_name or None,
            "error_code": error_code or None,
            "error_category": error_category or None,
        },
    )


def transition_resume_document_blocked(
    db: Any, run: Any, now: datetime, error: Exception, _retrying: bool
) -> None:
    """把结构化必要输入不足投影为可继续处理的简历确认任务。"""
    submission = db.get(ResumeSubmission, run.subject_id) if run.subject_id else None
    if submission is None:
        return
    document = db.get(SourceDocument, submission.source_document_id)
    candidate = db.get(Candidate, submission.candidate_id) if submission.candidate_id else None
    error_code = str(getattr(error, "error_code", ""))
    review_kind = (
        ResumeReviewKind.STRUCTURE_METADATA
        if error_code == "resume_metadata_review_required"
        else ResumeReviewKind.STRUCTURE_OUTLINE
    )
    CandidateIntakeProcessService.require_review(
        submission,
        review_kind=review_kind,
        reason=str(error)[:2000],
        document=document,
        candidate=candidate,
        now=now,
        recovery_code=ResumeRecoveryCode.STRUCTURE_INPUT_INSUFFICIENT.value,
        recovery_context={"blocked_step": "structure_resume", "blocked_code": error_code},
    )


def _resume_failure_recovery_code(*, error_code: str, error_text: str, step_name: str) -> str:
    """Map a terminal resume Step failure to one concrete self-service recovery."""
    code = error_code.casefold()
    text = error_text.casefold()
    step = step_name.casefold()
    combined = f"{code}:{text}"
    # 历史检查点有时只有 error_code，没有 step_name；两者都参与判断，
    # 避免发布失败被误归类为重新解析。
    if step == "publish_resume_result" or any(
        marker in combined
        for marker in (
            "publish_resume_result",
            "resume_publish",
            "resume_profile_publish",
            "publish:integrityerror",
            "publish:operationalerror",
        )
    ):
        return ResumeRecoveryCode.PUBLISH_RETRYABLE.value
    if step == "freeze_resume_sources" or any(
        marker in combined
        for marker in ("resume_source_document_not_found", "source_document_not_found", "object_not_found", "no_such_key")
    ):
        return ResumeRecoveryCode.SOURCE_UNAVAILABLE.value
    if any(marker in combined for marker in ("invalid_pdf", "invalid file", "filetype", "page_limit_exceeded")):
        return ResumeRecoveryCode.INVALID_SOURCE_FILE.value
    if step == "parse_resume_document" or any(
        marker in combined for marker in ("parse_resume_document", "mineru", "resume_parse")
    ):
        if any(marker in combined for marker in ("quality_rejected", "parse_quality", "empty", "no_valid_parser_result")):
            return ResumeRecoveryCode.PARSE_QUALITY_REJECTED.value
        return ResumeRecoveryCode.PARSE_RETRY_EXHAUSTED.value
    if step == "structure_resume" or "resume_structure" in combined:
        return ResumeRecoveryCode.STRUCTURE_FAILED.value
    if step in {"candidate_routing", "route_candidate", "candidate_routing_workflow"} or any(
        marker in combined
        for marker in ("candidate_routing", "routing_workflow", "candidate_major_routing")
    ):
        return ResumeRecoveryCode.ROUTING_RETRYABLE.value
    return ResumeRecoveryCode.PARSE_RETRY_EXHAUSTED.value


def _transition_args(context: WorkflowTransitionContext):
    """提取运行时统一传入的失败处理参数。"""
    return (
        context.metadata["db"],
        context.metadata["run"],
        context.metadata["now"],
        context.error,
        context.retrying,
    )


def handle_job_document_transition(context: WorkflowTransitionContext) -> None:
    """Job 文档 Workflow 的运行时失败入口。"""
    db, run, now, error, retrying = _transition_args(context)
    # 终态失败常被运行时包装成 RuntimeError，优先使用 StepRunner 传入的稳定错误码。
    if context.metadata.get("error_code"):
        error = type("WorkflowError", (RuntimeError,), {
            "error_code": context.metadata.get("error_code"),
            "step_name": context.metadata.get("step_name"),
        })(str(error))
    transition_job_document(db, run, now, error, retrying)


def handle_job_document_blocked(context: WorkflowTransitionContext) -> None:
    """岗位文档无法可靠识别时进入人工确认，而不是技术失败。"""
    db: Any = context.metadata["db"]
    run: Any = context.metadata["run"]
    import_task = db.get(JobDocumentImport, run.subject_id) if run.subject_id else None
    if import_task is None:
        return
    blocked_code = str(context.metadata.get("error_code") or "")
    recovery_code = (
        JobRecoveryCode.HEADER_REVIEW_REQUIRED.value
        if "header" in blocked_code
        else JobRecoveryCode.FIELD_REVIEW_REQUIRED.value
    )
    recovery_context: dict[str, Any] = {
        "blockedStep": context.metadata.get("step_name"),
        "errorCode": blocked_code or None,
    }
    # 表头人工确认需要看到真实候选列；从对象存储重新读取前 20 行即可，
    # 读取失败时仍保留恢复按钮，用户可直接重新上传文件。
    if recovery_code == JobRecoveryCode.HEADER_REVIEW_REQUIRED.value:
        document = db.get(SourceDocument, import_task.source_document_id)
        try:
            if document is not None:
                path = ObjectStore().materialize(document.object_ref, f"documents/{document.source_document_id}/{document.original_filename}")
                workbook = ExcelDocumentParser().parse(path.read_bytes(), document.original_filename)
                recovery_context["sheets"] = [
                    {"name": sheet.name, "rows": sheet.rows[:20]}
                    for sheet in workbook.sheets
                ]
                recovery_context["requiredFields"] = ["title", "department", "headcount", "responsibilities", "qualifications", "education", "major"]
        except Exception:
            recovery_context["sourcePreviewUnavailable"] = True
    JobDocumentImportProcessService.require_review(
        import_task,
        review_kind="headers" if "header" in blocked_code else "fields",
        reason=str(context.error)[:2000],
        recovery_code=recovery_code,
        recovery_context=recovery_context,
        now=context.metadata["now"],
    )


def handle_resume_document_transition(context: WorkflowTransitionContext) -> None:
    """Resume 文档 Workflow 的运行时失败入口。"""
    db, run, now, error, retrying = _transition_args(context)
    transition_resume_document(
        db,
        run,
        now,
        error,
        retrying,
        error_code=str(context.metadata.get("error_code") or ""),
        error_category=str(context.metadata.get("error_category") or ""),
        step_name=str(context.metadata.get("step_name") or ""),
    )


def handle_resume_document_blocked(context: WorkflowTransitionContext) -> None:
    """Resume Workflow 的业务阻塞入口；与技术失败入口严格分离。"""
    transition_resume_document_blocked(*_transition_args(context))
