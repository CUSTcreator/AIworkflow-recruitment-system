"""岗位链路的自助恢复合同。

恢复码描述业务上发生了什么，动作描述用户下一步可以执行什么；页面只消费动作
DTO，不根据数据库状态猜按钮。动作目录保持稳定，便于旧客户端安全忽略新动作。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from backend.app.shared.recovery_actions import RecoveryActionSpec, public_recovery_actions


class JobRecoveryCode(StrEnum):
    SOURCE_UNAVAILABLE = "job_source_unavailable"
    INVALID_SOURCE = "job_invalid_source"
    EXTRACTION_RETRYABLE = "job_extraction_retryable"
    HEADER_REVIEW_REQUIRED = "job_header_review_required"
    FIELD_REVIEW_REQUIRED = "job_field_review_required"
    DRAFT_PUBLISH_RETRYABLE = "job_draft_publish_retryable"
    DRAFT_REVIEW_REQUIRED = "job_draft_review_required"
    VERSION_PUBLISH_RETRYABLE = "job_version_publish_retryable"
    PROFILE_RETRYABLE = "job_profile_retryable"
    PROFILE_REVIEW_REQUIRED = "job_profile_review_required"
    PROFILE_READY_RETRYABLE = "job_profile_ready_retryable"
    APPLICATION_RELEASE_RETRYABLE = "job_application_release_retryable"


JOB_RECOVERY_ACTIONS: dict[str, RecoveryActionSpec] = {
    "retry_job_extraction": RecoveryActionSpec("retry_job_extraction", "重新解析岗位文件", retry_scope="extract_job_document"),
    "upload_replacement_job": RecoveryActionSpec("upload_replacement_job", "重新上传岗位文件", requires_input=True, retry_scope="upload_job_document"),
    "review_job_headers": RecoveryActionSpec("review_job_headers", "确认岗位表头", requires_input=True, retry_scope="extract_job_document"),
    "review_job_fields": RecoveryActionSpec("review_job_fields", "校正岗位字段", requires_input=True, retry_scope="extract_job_document"),
    "retry_publish_job_drafts": RecoveryActionSpec("retry_publish_job_drafts", "重新发布岗位草稿", retry_scope="publish_job_drafts"),
    "review_job_drafts": RecoveryActionSpec("review_job_drafts", "查看并确认岗位", requires_input=True, retry_scope="confirm_job_drafts"),
    "edit_job_draft": RecoveryActionSpec("edit_job_draft", "编辑岗位字段", requires_input=True, retry_scope="update_job_draft"),
    "select_job_department": RecoveryActionSpec("select_job_department", "选择所属部门", requires_input=True, retry_scope="update_job_draft"),
    "resolve_duplicate_job": RecoveryActionSpec("resolve_duplicate_job", "处理重复岗位", requires_input=True, retry_scope="confirm_job_drafts"),
    "confirm_job_draft": RecoveryActionSpec("confirm_job_draft", "确认岗位", retry_scope="confirm_job_drafts"),
    "skip_job_draft": RecoveryActionSpec("skip_job_draft", "跳过岗位", retry_scope="confirm_job_drafts"),
    "retry_job_version": RecoveryActionSpec("retry_job_version", "重试创建岗位版本", retry_scope="create_job_version"),
    "retry_job_profile": RecoveryActionSpec("retry_job_profile", "重试生成岗位画像", retry_scope="job_profile_compilation"),
    # 当前用户通过编辑岗位源要求来校正画像输入；保存后由既有版本/画像流程重建。
    "edit_job_profile": RecoveryActionSpec("edit_job_profile", "编辑岗位源要求", requires_input=True, retry_scope="job_profile_compilation"),
    "regenerate_job_profile": RecoveryActionSpec("regenerate_job_profile", "重新生成岗位画像", retry_scope="job_profile_compilation"),
    "retry_mark_job_ready": RecoveryActionSpec("retry_mark_job_ready", "重试发布岗位画像", retry_scope="mark_job_version_ready"),
    "retry_release_waiting_applications": RecoveryActionSpec("retry_release_waiting_applications", "重试继续处理候选人", retry_scope="release_waiting_applications"),
}


def job_recovery_actions(codes: list[str] | tuple[str, ...]) -> list[dict[str, object]]:
    return public_recovery_actions(codes, JOB_RECOVERY_ACTIONS)


@dataclass(frozen=True, slots=True)
class JobRecoveryPlan:
    code: str
    mode: str
    public_message: str
    actions: tuple[str, ...]


_PLANS: dict[str, JobRecoveryPlan] = {
    JobRecoveryCode.SOURCE_UNAVAILABLE.value: JobRecoveryPlan(JobRecoveryCode.SOURCE_UNAVAILABLE.value, "user_recovery", "岗位原始文件不可用，请重新上传。", ("upload_replacement_job",)),
    JobRecoveryCode.INVALID_SOURCE.value: JobRecoveryPlan(JobRecoveryCode.INVALID_SOURCE.value, "user_recovery", "该文件无法读取，请上传有效的 Excel 岗位文件。", ("upload_replacement_job",)),
    JobRecoveryCode.EXTRACTION_RETRYABLE.value: JobRecoveryPlan(JobRecoveryCode.EXTRACTION_RETRYABLE.value, "user_recovery", "岗位文件解析未完成，可重新解析或重新上传。", ("retry_job_extraction", "upload_replacement_job")),
    JobRecoveryCode.HEADER_REVIEW_REQUIRED.value: JobRecoveryPlan(JobRecoveryCode.HEADER_REVIEW_REQUIRED.value, "user_recovery", "未能可靠识别岗位表头，请确认表头后继续。", ("review_job_headers", "retry_job_extraction", "upload_replacement_job")),
    JobRecoveryCode.FIELD_REVIEW_REQUIRED.value: JobRecoveryPlan(JobRecoveryCode.FIELD_REVIEW_REQUIRED.value, "user_recovery", "部分岗位字段需要确认，请校正后继续。", ("review_job_fields", "retry_job_extraction", "upload_replacement_job")),
    JobRecoveryCode.DRAFT_PUBLISH_RETRYABLE.value: JobRecoveryPlan(JobRecoveryCode.DRAFT_PUBLISH_RETRYABLE.value, "artifact_retry", "岗位已解析，发布草稿暂未完成，可直接重试发布。", ("retry_publish_job_drafts", "review_job_drafts")),
    JobRecoveryCode.DRAFT_REVIEW_REQUIRED.value: JobRecoveryPlan(JobRecoveryCode.DRAFT_REVIEW_REQUIRED.value, "user_recovery", "岗位草稿需要人工确认。", ("review_job_drafts",)),
    JobRecoveryCode.VERSION_PUBLISH_RETRYABLE.value: JobRecoveryPlan(JobRecoveryCode.VERSION_PUBLISH_RETRYABLE.value, "artifact_retry", "岗位已确认，创建岗位版本暂未完成，可重试。", ("retry_job_version", "review_job_drafts")),
    JobRecoveryCode.PROFILE_RETRYABLE.value: JobRecoveryPlan(JobRecoveryCode.PROFILE_RETRYABLE.value, "artifact_retry", "岗位画像生成未完成，可重试或校正后重新生成。", ("retry_job_profile", "edit_job_profile")),
    JobRecoveryCode.PROFILE_REVIEW_REQUIRED.value: JobRecoveryPlan(JobRecoveryCode.PROFILE_REVIEW_REQUIRED.value, "user_recovery", "岗位能力画像覆盖不足，请校正画像后继续。", ("edit_job_profile", "retry_job_profile")),
    JobRecoveryCode.PROFILE_READY_RETRYABLE.value: JobRecoveryPlan(JobRecoveryCode.PROFILE_READY_RETRYABLE.value, "artifact_retry", "岗位画像已生成但发布未完成，可重试发布。", ("retry_mark_job_ready", "retry_job_profile")),
    JobRecoveryCode.APPLICATION_RELEASE_RETRYABLE.value: JobRecoveryPlan(JobRecoveryCode.APPLICATION_RELEASE_RETRYABLE.value, "artifact_retry", "岗位画像已就绪，候选人任务释放未完成，可重试继续处理。", ("retry_release_waiting_applications",)),
}


def job_recovery_plan(code: str | None, *, has_extraction: bool = False, has_profile: bool = False) -> JobRecoveryPlan:
    """按已持久化工件过滤动作，避免返回必然失败的按钮。"""
    normalized = str(code or "").strip()
    plan = _PLANS.get(normalized) or JobRecoveryPlan(
        normalized or JobRecoveryCode.EXTRACTION_RETRYABLE.value,
        "user_recovery",
        "岗位处理未完成，可重新解析或重新上传。",
        ("retry_job_extraction", "upload_replacement_job"),
    )
    actions = list(plan.actions)
    if "retry_publish_job_drafts" in actions and not has_extraction:
        actions.remove("retry_publish_job_drafts")
    if "retry_mark_job_ready" in actions and not has_profile:
        actions.remove("retry_mark_job_ready")
    return JobRecoveryPlan(plan.code, plan.mode, plan.public_message, tuple(actions))


def job_recovery_view(code: str | None, *, has_extraction: bool = False, has_profile: bool = False, context: dict[str, Any] | None = None) -> dict[str, Any]:
    plan = job_recovery_plan(code, has_extraction=has_extraction, has_profile=has_profile)
    return {"issue_code": plan.code, "mode": plan.mode, "display_message": plan.public_message, "allowed_actions": job_recovery_actions(plan.actions), "context": dict(context or {})}
