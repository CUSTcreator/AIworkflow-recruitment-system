"""Resume-specific recovery contract shared by Workflow transitions and Intake DTOs."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.app.modules.candidates.domain.resume_submission_state_machine import (
    ResumeRecoveryCode,
)
from backend.app.shared.recovery_actions import RecoveryActionSpec, public_recovery_actions


RESUME_RECOVERY_ACTIONS: dict[str, RecoveryActionSpec] = {
    "force_fresh_parse": RecoveryActionSpec(
        "force_fresh_parse", "重新解析", retry_scope="parse_resume_document"
    ),
    "upload_replacement_resume": RecoveryActionSpec(
        "upload_replacement_resume", "重新上传", requires_input=True,
        retry_scope="upload_resume",
    ),
    "correct_parsed_resume": RecoveryActionSpec(
        "correct_parsed_resume", "查看并校正", requires_input=True,
        retry_scope="structure_resume",
    ),
    "retry_publish_resume": RecoveryActionSpec(
        "retry_publish_resume", "重新发布简历画像",
        retry_scope="publish_resume_result",
    ),
    "retry_routing": RecoveryActionSpec(
        "retry_routing", "重新匹配岗位", retry_scope="match_candidate_jobs"
    ),
    "create_applications": RecoveryActionSpec(
        "create_applications", "人工选择岗位", requires_input=True,
        retry_scope="publish_applications",
    ),
    "resolve_duplicate_candidates": RecoveryActionSpec(
        "resolve_duplicate_candidates", "选择候选人归属", requires_input=True,
        retry_scope="resolve_candidate_identity",
    ),
    "replace_duplicate_resume": RecoveryActionSpec(
        "replace_duplicate_resume", "用本次上传的简历更新候选人", requires_input=True,
        warning="采用后将更新候选人的当前简历，并重新计算仍在流程中的岗位申请。",
        retry_scope="publish_resume_result",
    ),
    "discard_submission": RecoveryActionSpec(
        "discard_submission", "放弃本次导入",
        warning="放弃后不会更新已有候选人和岗位申请。",
        retry_scope="resolve_candidate_identity",
    ),
}


def resume_recovery_actions(action_codes: list[str] | tuple[str, ...]) -> list[dict[str, object]]:
    """Build the structured action DTO used by both list and detail views."""
    return public_recovery_actions(action_codes, RESUME_RECOVERY_ACTIONS)


@dataclass(frozen=True, slots=True)
class ResumeRecoveryPlan:
    """One actionable, public recovery decision for a ResumeSubmission."""

    code: str
    mode: str
    public_message: str
    actions: tuple[str, ...]


_PLANS: dict[str, ResumeRecoveryPlan] = {
    ResumeRecoveryCode.SOURCE_UNAVAILABLE.value: ResumeRecoveryPlan(
        ResumeRecoveryCode.SOURCE_UNAVAILABLE.value,
        "user_recovery",
        "原始简历文件已不可用，请重新上传简历后继续。",
        ("upload_replacement_resume",),
    ),
    ResumeRecoveryCode.INVALID_SOURCE_FILE.value: ResumeRecoveryPlan(
        ResumeRecoveryCode.INVALID_SOURCE_FILE.value,
        "user_recovery",
        "该文件无法作为简历解析，请上传可读取的 PDF 简历。",
        ("upload_replacement_resume",),
    ),
    ResumeRecoveryCode.PARSE_QUALITY_REJECTED.value: ResumeRecoveryPlan(
        ResumeRecoveryCode.PARSE_QUALITY_REJECTED.value,
        "user_recovery",
        "简历内容未能可靠解析。可重新解析，或上传更清晰的简历。",
        ("force_fresh_parse", "upload_replacement_resume"),
    ),
    ResumeRecoveryCode.PARSE_RETRY_EXHAUSTED.value: ResumeRecoveryPlan(
        ResumeRecoveryCode.PARSE_RETRY_EXHAUSTED.value,
        "user_recovery",
        "简历解析暂未完成。可重新发起解析，或上传替换简历。",
        ("force_fresh_parse", "upload_replacement_resume"),
    ),
    ResumeRecoveryCode.STRUCTURE_INPUT_INSUFFICIENT.value: ResumeRecoveryPlan(
        ResumeRecoveryCode.STRUCTURE_INPUT_INSUFFICIENT.value,
        "user_recovery",
        "已有解析文本但结构不足以可靠评分。请校正原文结构，或重新解析、重新上传。",
        ("correct_parsed_resume", "force_fresh_parse", "upload_replacement_resume"),
    ),
    ResumeRecoveryCode.STRUCTURE_FAILED.value: ResumeRecoveryPlan(
        ResumeRecoveryCode.STRUCTURE_FAILED.value,
        "user_recovery",
        "简历结构化未完成。可基于解析原文校正，或重新解析、重新上传。",
        ("correct_parsed_resume", "force_fresh_parse", "upload_replacement_resume"),
    ),
    ResumeRecoveryCode.PUBLISH_RETRYABLE.value: ResumeRecoveryPlan(
        ResumeRecoveryCode.PUBLISH_RETRYABLE.value,
        "artifact_retry",
        "简历信息已提取，发布结果暂未完成。可直接重新发布，无需重新解析。",
        ("retry_publish_resume", "correct_parsed_resume", "force_fresh_parse", "upload_replacement_resume"),
    ),
    ResumeRecoveryCode.ROUTING_RETRYABLE.value: ResumeRecoveryPlan(
        ResumeRecoveryCode.ROUTING_RETRYABLE.value,
        "artifact_retry",
        "简历已完成处理，但岗位分发暂未完成。可重新匹配岗位或人工选择岗位。",
        ("retry_routing", "create_applications"),
    ),
    ResumeRecoveryCode.ROUTING_MANUAL_SELECTION.value: ResumeRecoveryPlan(
        ResumeRecoveryCode.ROUTING_MANUAL_SELECTION.value,
        "user_recovery",
        "未形成可靠的自动岗位匹配结果，请人工选择投递岗位。",
        ("create_applications", "retry_routing"),
    ),
    ResumeRecoveryCode.DUPLICATE_BLOCKED.value: ResumeRecoveryPlan(
        ResumeRecoveryCode.DUPLICATE_BLOCKED.value,
        "user_recovery",
        "该候选人正在招聘流程中，当前不能替换简历。可放弃本次导入或上传后续版本。",
        ("discard_submission", "upload_replacement_resume"),
    ),
}


def resume_recovery_plan(
    code: str | None,
    *,
    has_parsed_text: bool = False,
    has_structure_result: bool = False,
    has_structure_hash: bool = False,
) -> ResumeRecoveryPlan:
    """Return a plan whose actions are executable for the persisted artifacts."""
    normalized = str(code or "").strip()
    plan = _PLANS.get(normalized)
    if plan is None:
        plan = ResumeRecoveryPlan(
            normalized or ResumeRecoveryCode.PARSE_RETRY_EXHAUSTED.value,
            "user_recovery",
            "本次处理未完成。可重新发起简历解析或上传替换简历。",
            ("force_fresh_parse", "upload_replacement_resume"),
        )
    actions = list(plan.actions)
    if "correct_parsed_resume" in actions and not has_parsed_text:
        actions.remove("correct_parsed_resume")
    # 发布重试会复用解析文本和结构化结果；任一工件缺失时该按钮不能出现，
    # 否则页面会给出一个必然被 API 拒绝的操作。
    if "retry_publish_resume" in actions and (
        not has_parsed_text or not has_structure_result or not has_structure_hash
    ):
        actions.remove("retry_publish_resume")
    return ResumeRecoveryPlan(plan.code, plan.mode, plan.public_message, tuple(actions))


def resume_recovery_view(
    code: str | None,
    *,
    has_parsed_text: bool = False,
    has_structure_result: bool = False,
    has_structure_hash: bool = False,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    plan = resume_recovery_plan(
        code,
        has_parsed_text=has_parsed_text,
        has_structure_result=has_structure_result,
        has_structure_hash=has_structure_hash,
    )
    return {
        "issue_code": plan.code,
        "mode": plan.mode,
        "display_message": plan.public_message,
        "allowed_actions": resume_recovery_actions(plan.actions),
        "context": dict(context or {}),
    }
