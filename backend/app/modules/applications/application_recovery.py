"""Application 从画像就绪到 V1 发布之间的自助恢复合同。

领域服务只持久化稳定的恢复码和最小上下文；读模型再把恢复码翻译成动作。
页面只执行后端返回的动作 DTO，不能根据 Application 或 Workflow 状态猜按钮。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ApplicationRecoveryCode(StrEnum):
    INITIAL_ASSESSMENT_RELEASE_RETRYABLE = "initial_assessment_release_retryable"
    HARD_SCREENING_REVIEW_REQUIRED = "hard_screening_review_required"
    HARD_SCREENING_SOURCE_REVIEW_REQUIRED = "hard_screening_source_review_required"
    HARD_SCREENING_RESUME_SOURCE_REQUIRED = "hard_screening_resume_source_required"
    HARD_SCREENING_POLICY_REQUIRED = "hard_screening_policy_required"
    HARD_SCREENING_RETRYABLE = "hard_screening_retryable"
    HARD_SCREENING_PUBLISH_RETRYABLE = "hard_screening_publish_retryable"
    SCREENING_RESUME_SOURCE_REQUIRED = "screening_resume_source_required"
    SCREENING_JOB_PROFILE_REQUIRED = "screening_job_profile_required"
    SCREENING_MODEL_CONFIGURATION_REQUIRED = "screening_model_configuration_required"
    SCREENING_SOURCE_REVIEW_REQUIRED = "screening_source_review_required"
    SCREENING_RETRYABLE = "screening_retryable"
    SCREENING_PUBLISH_RETRYABLE = "screening_publish_retryable"
    FIRST_INTERVIEW_PLANNING_RETRYABLE = "first_interview_planning_retryable"
    FIRST_INTERVIEW_SOURCE_ASSESSMENT_REQUIRED = "first_interview_source_assessment_required"
    FIRST_INTERVIEW_SOURCE_PROFILE_REQUIRED = "first_interview_source_profile_required"
    FIRST_INTERVIEW_TARGET_REVIEW_REQUIRED = "first_interview_target_review_required"
    FIRST_INTERVIEW_CONFIGURATION_REQUIRED = "first_interview_configuration_required"
    FIRST_INTERVIEW_PUBLISH_RETRYABLE = "first_interview_publish_retryable"
    POST_FIRST_SOURCE_REVIEW_REQUIRED = "post_first_source_review_required"
    POST_FIRST_FEEDBACK_REVIEW_REQUIRED = "post_first_feedback_review_required"
    POST_FIRST_SCORING_RETRYABLE = "post_first_scoring_retryable"
    POST_FIRST_PUBLISH_RETRYABLE = "post_first_publish_retryable"
    POST_SECOND_SOURCE_REVIEW_REQUIRED = "post_second_source_review_required"
    POST_SECOND_FEEDBACK_REVIEW_REQUIRED = "post_second_feedback_review_required"
    POST_SECOND_SCORING_RETRYABLE = "post_second_scoring_retryable"
    POST_SECOND_PUBLISH_RETRYABLE = "post_second_publish_retryable"


@dataclass(frozen=True, slots=True)
class ApplicationRecoveryPlan:
    code: str
    mode: str
    public_message: str
    actions: tuple[str, ...]


_PLANS: dict[str, ApplicationRecoveryPlan] = {
    ApplicationRecoveryCode.INITIAL_ASSESSMENT_RELEASE_RETRYABLE.value: ApplicationRecoveryPlan(
        ApplicationRecoveryCode.INITIAL_ASSESSMENT_RELEASE_RETRYABLE.value,
        "artifact_retry",
        "岗位画像已就绪，但本申请尚未成功启动初步筛选，可直接重试。",
        ("retry_initial_assessment",),
    ),
    ApplicationRecoveryCode.HARD_SCREENING_REVIEW_REQUIRED.value: ApplicationRecoveryPlan(
        ApplicationRecoveryCode.HARD_SCREENING_REVIEW_REQUIRED.value,
        "user_recovery",
        "硬筛无法自动得出可靠结论，请人工核对后处理。",
        ("review_hard_screening_pass", "review_hard_screening_reject"),
    ),
    ApplicationRecoveryCode.HARD_SCREENING_SOURCE_REVIEW_REQUIRED.value: ApplicationRecoveryPlan(
        ApplicationRecoveryCode.HARD_SCREENING_SOURCE_REVIEW_REQUIRED.value,
        "user_recovery",
        "硬筛输入未能可靠冻结，可重新运行或人工处理。",
        ("retry_hard_screening", "review_hard_screening_pass", "review_hard_screening_reject"),
    ),
    ApplicationRecoveryCode.HARD_SCREENING_RESUME_SOURCE_REQUIRED.value: ApplicationRecoveryPlan(
        ApplicationRecoveryCode.HARD_SCREENING_RESUME_SOURCE_REQUIRED.value,
        "user_recovery",
        "硬筛缺少可靠的简历事实，请重新解析或校正简历；必要时也可人工处理。",
        ("repair_resume_source", "review_hard_screening_pass", "review_hard_screening_reject"),
    ),
    ApplicationRecoveryCode.HARD_SCREENING_POLICY_REQUIRED.value: ApplicationRecoveryPlan(
        ApplicationRecoveryCode.HARD_SCREENING_POLICY_REQUIRED.value,
        "user_recovery",
        "硬筛规则不可用，请处理岗位配置；必要时也可人工处理。",
        ("repair_hard_screening_policy", "review_hard_screening_pass", "review_hard_screening_reject"),
    ),
    ApplicationRecoveryCode.HARD_SCREENING_RETRYABLE.value: ApplicationRecoveryPlan(
        ApplicationRecoveryCode.HARD_SCREENING_RETRYABLE.value,
        "artifact_retry",
        "硬筛处理未完成，可重新运行；也可直接人工处理避免流程卡住。",
        ("retry_hard_screening", "review_hard_screening_pass", "review_hard_screening_reject"),
    ),
    ApplicationRecoveryCode.HARD_SCREENING_PUBLISH_RETRYABLE.value: ApplicationRecoveryPlan(
        ApplicationRecoveryCode.HARD_SCREENING_PUBLISH_RETRYABLE.value,
        "artifact_retry",
        "硬筛结果发布未完成，可重新运行发布或人工处理。",
        ("retry_hard_screening", "review_hard_screening_pass", "review_hard_screening_reject"),
    ),
    ApplicationRecoveryCode.SCREENING_RESUME_SOURCE_REQUIRED.value: ApplicationRecoveryPlan(
        ApplicationRecoveryCode.SCREENING_RESUME_SOURCE_REQUIRED.value,
        "user_recovery",
        "初步筛选缺少可靠的简历来源，请处理简历后重新运行。",
        ("repair_resume_source",),
    ),
    ApplicationRecoveryCode.SCREENING_JOB_PROFILE_REQUIRED.value: ApplicationRecoveryPlan(
        ApplicationRecoveryCode.SCREENING_JOB_PROFILE_REQUIRED.value,
        "user_recovery",
        "初步筛选缺少可评分的岗位画像，请处理岗位画像后重新运行。",
        ("repair_job_profile",),
    ),
    ApplicationRecoveryCode.SCREENING_MODEL_CONFIGURATION_REQUIRED.value: ApplicationRecoveryPlan(
        ApplicationRecoveryCode.SCREENING_MODEL_CONFIGURATION_REQUIRED.value,
        "user_recovery",
        "岗位评分模型配置不可用，系统会在重新处理时使用有效默认模型。",
        ("run_scoring",),
    ),
    ApplicationRecoveryCode.SCREENING_SOURCE_REVIEW_REQUIRED.value: ApplicationRecoveryPlan(
        ApplicationRecoveryCode.SCREENING_SOURCE_REVIEW_REQUIRED.value,
        "user_recovery",
        "初步筛选来源无法可靠绑定，请检查简历和岗位画像后重新运行。",
        ("repair_resume_source", "repair_job_profile"),
    ),
    ApplicationRecoveryCode.SCREENING_RETRYABLE.value: ApplicationRecoveryPlan(
        ApplicationRecoveryCode.SCREENING_RETRYABLE.value,
        "artifact_retry",
        "初步筛选未完成，可从失败位置重新运行。",
        ("run_scoring",),
    ),
    ApplicationRecoveryCode.SCREENING_PUBLISH_RETRYABLE.value: ApplicationRecoveryPlan(
        ApplicationRecoveryCode.SCREENING_PUBLISH_RETRYABLE.value,
        "artifact_retry",
        "初步筛选结果已生成，但发布未完成，可直接重新发布。",
        ("run_scoring",),
    ),
    ApplicationRecoveryCode.FIRST_INTERVIEW_PLANNING_RETRYABLE.value: ApplicationRecoveryPlan(
        ApplicationRecoveryCode.FIRST_INTERVIEW_PLANNING_RETRYABLE.value,
        "artifact_retry",
        "一面题单规划未完成，可重新生成或改用人工题纲继续。",
        ("run_first_interview_planning", "continue_first_interview_manually"),
    ),
    ApplicationRecoveryCode.FIRST_INTERVIEW_SOURCE_ASSESSMENT_REQUIRED.value: ApplicationRecoveryPlan(
        ApplicationRecoveryCode.FIRST_INTERVIEW_SOURCE_ASSESSMENT_REQUIRED.value,
        "user_recovery",
        "缺少可追溯的初步筛选结果，请先检查初步筛选结果再重新生成题单。",
        ("review_screening_result", "run_first_interview_planning"),
    ),
    ApplicationRecoveryCode.FIRST_INTERVIEW_SOURCE_PROFILE_REQUIRED.value: ApplicationRecoveryPlan(
        ApplicationRecoveryCode.FIRST_INTERVIEW_SOURCE_PROFILE_REQUIRED.value,
        "user_recovery",
        "初步筛选引用的简历或岗位画像不可用，可检查初步筛选来源后重试，或改用人工题纲继续。",
        ("review_screening_result", "run_first_interview_planning", "continue_first_interview_manually"),
    ),
    ApplicationRecoveryCode.FIRST_INTERVIEW_TARGET_REVIEW_REQUIRED.value: ApplicationRecoveryPlan(
        ApplicationRecoveryCode.FIRST_INTERVIEW_TARGET_REVIEW_REQUIRED.value,
        "user_recovery",
        "待核验目标不符合题单生成限制，请先检查初步筛选目标，或改用人工题纲继续。",
        ("review_screening_result", "continue_first_interview_manually"),
    ),
    ApplicationRecoveryCode.FIRST_INTERVIEW_CONFIGURATION_REQUIRED.value: ApplicationRecoveryPlan(
        ApplicationRecoveryCode.FIRST_INTERVIEW_CONFIGURATION_REQUIRED.value,
        "user_recovery",
        "题单生成配置无效，可按默认配置重新生成，或改用人工题纲继续。",
        ("run_first_interview_planning", "continue_first_interview_manually"),
    ),
    ApplicationRecoveryCode.FIRST_INTERVIEW_PUBLISH_RETRYABLE.value: ApplicationRecoveryPlan(
        ApplicationRecoveryCode.FIRST_INTERVIEW_PUBLISH_RETRYABLE.value,
        "artifact_retry",
        "题目和展示内容已生成，但题单草稿发布未完成，可直接重新发布。",
        ("run_first_interview_planning", "continue_first_interview_manually"),
    ),
    ApplicationRecoveryCode.POST_FIRST_SOURCE_REVIEW_REQUIRED.value: ApplicationRecoveryPlan(
        ApplicationRecoveryCode.POST_FIRST_SOURCE_REVIEW_REQUIRED.value,
        "user_recovery",
        "当前评估缺少可用的初步筛选依据或冻结画像。请先修复来源，再重新计算。",
        ("rebuild_screening_assessment", "retry_post_first_scoring"),
    ),
    ApplicationRecoveryCode.POST_FIRST_FEEDBACK_REVIEW_REQUIRED.value: ApplicationRecoveryPlan(
        ApplicationRecoveryCode.POST_FIRST_FEEDBACK_REVIEW_REQUIRED.value,
        "user_recovery",
        "一面记录无法形成可靠的评分输入，请修改一面记录后重新计算。",
        ("edit_first_interview_feedback",),
    ),
    ApplicationRecoveryCode.POST_FIRST_SCORING_RETRYABLE.value: ApplicationRecoveryPlan(
        ApplicationRecoveryCode.POST_FIRST_SCORING_RETRYABLE.value,
        "artifact_retry",
        "当前评估未完成，可从失败处继续计算，也可以修改一面记录后重新计算。",
        ("retry_post_first_scoring", "edit_first_interview_feedback"),
    ),
    ApplicationRecoveryCode.POST_FIRST_PUBLISH_RETRYABLE.value: ApplicationRecoveryPlan(
        ApplicationRecoveryCode.POST_FIRST_PUBLISH_RETRYABLE.value,
        "artifact_retry",
        "当前评估已完成计算但发布未完成，可继续发布。",
        ("retry_post_first_scoring",),
    ),
    ApplicationRecoveryCode.POST_SECOND_SOURCE_REVIEW_REQUIRED.value: ApplicationRecoveryPlan(
        ApplicationRecoveryCode.POST_SECOND_SOURCE_REVIEW_REQUIRED.value,
        "user_recovery",
        "当前评估缺少可用的上一轮评估依据或冻结画像。请先修复来源，再重新计算。",
        ("rebuild_previous_post_first_assessment", "retry_post_second_scoring"),
    ),
    ApplicationRecoveryCode.POST_SECOND_FEEDBACK_REVIEW_REQUIRED.value: ApplicationRecoveryPlan(
        ApplicationRecoveryCode.POST_SECOND_FEEDBACK_REVIEW_REQUIRED.value,
        "user_recovery",
        "二面记录无法形成可靠的评分输入，请修改二面记录后重新计算。",
        ("edit_second_interview_feedback",),
    ),
    ApplicationRecoveryCode.POST_SECOND_SCORING_RETRYABLE.value: ApplicationRecoveryPlan(
        ApplicationRecoveryCode.POST_SECOND_SCORING_RETRYABLE.value,
        "artifact_retry",
        "当前评估未完成，可从失败处继续计算，也可以修改二面记录后重新计算。",
        ("retry_post_second_scoring", "edit_second_interview_feedback"),
    ),
    ApplicationRecoveryCode.POST_SECOND_PUBLISH_RETRYABLE.value: ApplicationRecoveryPlan(
        ApplicationRecoveryCode.POST_SECOND_PUBLISH_RETRYABLE.value,
        "artifact_retry",
        "当前评估已完成计算但发布未完成，可继续发布。",
        ("retry_post_second_scoring",),
    ),
}


def application_recovery_plan(code: str | None) -> ApplicationRecoveryPlan | None:
    """返回已登记的稳定恢复方案；未知码不得猜测所属流程阶段。

    恢复码可能来自 V1、V2 或 V3。将未知码统一降级成 ``run_scoring`` 会在
    后续阶段展示错误按钮，因此未知码只保留异常提示和诊断信息，不生成动作。
    """

    normalized = str(code or "").strip()
    return _PLANS.get(normalized)


def set_application_recovery(
    application,
    code: ApplicationRecoveryCode | str,
    *,
    context: dict[str, object] | None = None,
) -> None:
    application.recovery_code = code.value if isinstance(code, ApplicationRecoveryCode) else str(code)
    application.recovery_context_json = dict(context or {})


def clear_application_recovery(application) -> None:
    application.recovery_code = None
    application.recovery_context_json = {}


def clear_application_recovery_for_prefix(application, prefix: str) -> bool:
    """只清除当前操作所拥有阶段的恢复事实。

    V3 的来源修复会先重算 V2；此时 V2 的受理和发布都不能把尚未恢复的
    ``post_second_*`` 状态提前清掉。返回值仅供调用方判断是否发生清理。
    """

    if not str(application.recovery_code or "").startswith(prefix):
        return False
    clear_application_recovery(application)
    return True
