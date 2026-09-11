"""Workflow 错误恢复策略的唯一约定入口。

本文件不替代业务 Workflow 的错误判断：handler 仍负责判断“是否需要人工确认”。
它只把稳定错误码翻译为统一的恢复动作和安全文案，供 StepRunner、流程 DTO
与前端使用。无法自动修复的错误也必须落到用户可执行的重新发起动作，不能把
投递流程永久交给技术人员处理。
"""
from __future__ import annotations

from dataclasses import dataclass

from .step_contracts import RecoveryAction, StepErrorCategory, StepOutcomeKind


@dataclass(frozen=True, slots=True)
class RecoveryDecision:
    """单个错误在业务页面的恢复合同。"""

    action: RecoveryAction
    public_message: str

    @property
    def can_retry(self) -> bool:
        return self.action == RecoveryAction.USER_RETRY


_CODE_DECISIONS: dict[str, RecoveryDecision] = {
    "resume_structure_review_required": RecoveryDecision(RecoveryAction.REVIEW_REQUIRED, "简历结构化结果需要人工确认后才能继续。"),
    "post_interview_parse_review_required": RecoveryDecision(RecoveryAction.REVIEW_REQUIRED, "面评内容需要人工核对后重新计算。"),
    "candidate_no_eligible_job": RecoveryDecision(RecoveryAction.REVIEW_REQUIRED, "未自动匹配到可用岗位，请人工选择岗位。"),
    "hard_screening_policy_missing": RecoveryDecision(RecoveryAction.USER_RETRY, "岗位缺少硬筛规则，完成配置后可重试。"),
    "application_frozen_resume_profile_missing": RecoveryDecision(RecoveryAction.USER_RETRY, "简历画像不可用，请重新解析简历后重试。"),
    "application_frozen_job_profile_missing": RecoveryDecision(RecoveryAction.USER_RETRY, "岗位画像不可用，请重新生成岗位画像后重试。"),
    "screening_job_capabilities_missing": RecoveryDecision(RecoveryAction.USER_RETRY, "岗位尚未生成可评分能力，完成岗位配置后可重试。"),
    # 重新发起评分会重新冻结当前简历、岗位版本和部署配置；这些错误不应
    # 把用户永久卡在“联系管理员”提示上。
    "screening_preset_model_unavailable": RecoveryDecision(RecoveryAction.USER_RETRY, "评分能力模型暂不可用，修复岗位配置后可重新运行初步筛选。"),
    "education_ranking_dataset_incomplete": RecoveryDecision(RecoveryAction.USER_RETRY, "院校排名数据暂未就绪，系统会按保守学历基准处理，确认后可重新运行初步筛选。"),
    "screening_source_projection_invalid": RecoveryDecision(RecoveryAction.USER_RETRY, "评分来源需要重新校验，可重新运行初步筛选。"),
    "screening_resume_profile_candidate_mismatch": RecoveryDecision(RecoveryAction.USER_RETRY, "简历画像版本不一致，可重新解析简历后重新运行初步筛选。"),
    "screening_job_profile_binding_mismatch": RecoveryDecision(RecoveryAction.USER_RETRY, "岗位画像版本不一致，可重新生成岗位画像后重新运行初步筛选。"),
    "screening_job_version_binding_mismatch": RecoveryDecision(RecoveryAction.USER_RETRY, "岗位版本已变化，可重新运行初步筛选。"),
}


def resolve_recovery(
    *,
    error_code: str | None,
    error_category: StepErrorCategory | str | None,
    outcome_kind: StepOutcomeKind | None = None,
    declared_action: RecoveryAction | None = None,
) -> RecoveryDecision:
    """返回统一恢复动作。

    优先级为 Workflow 显式声明、稳定错误码、运行时类别默认值。只有 ``blocked``
    才会默认等待用户确认；未知终态失败也提供用户可执行的重新发起动作。
    """
    if isinstance(error_category, str):
        try:
            error_category = StepErrorCategory(error_category)
        except ValueError:
            error_category = None
    if declared_action is not None:
        return RecoveryDecision(declared_action, _message_for_action(declared_action))
    if error_code and error_code in _CODE_DECISIONS:
        return _CODE_DECISIONS[error_code]
    if outcome_kind in {StepOutcomeKind.RETRY_WAIT, StepOutcomeKind.ACTIVITY_RETRY_WAIT, StepOutcomeKind.WAITING_EXTERNAL}:
        return RecoveryDecision(RecoveryAction.AUTO_RETRY, "当前步骤暂时不可用，系统将自动重试。")
    if outcome_kind == StepOutcomeKind.BLOCKED:
        return RecoveryDecision(RecoveryAction.REVIEW_REQUIRED, "当前步骤已暂停，请确认或补充信息后继续。")
    if error_category == StepErrorCategory.EXTERNAL_PERMANENT:
        return RecoveryDecision(RecoveryAction.USER_RETRY, "外部服务未能完成处理，可在确认服务恢复后重试。")
    return RecoveryDecision(RecoveryAction.USER_RETRY, "当前流程未完成，可重新发起处理；系统会复用已完成的步骤。")


def _message_for_action(action: RecoveryAction) -> str:
    return {
        RecoveryAction.AUTO_RETRY: "当前步骤暂时不可用，系统将自动重试。",
        RecoveryAction.USER_RETRY: "当前步骤处理失败，修复相关信息后可重试。",
        RecoveryAction.REVIEW_REQUIRED: "当前步骤待确认，请确认或补充信息后继续。",
        RecoveryAction.CONTINUE_MANUALLY: "自动处理未完成，可转为人工继续。",
    }[action]
