from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.entities import (
    Application,
    ApplicationAssessmentVersion,
    ResumeSubmission,
    User,
    WorkflowRun,
)
from backend.app.modules.auth.public import AuthorizationService
from backend.app.shared.recovery_actions import RecoveryActionSpec, public_recovery_actions
from backend.app.modules.applications.domain.application_lifecycle_policy import is_application_closed


APPLICATION_RECOVERY_ACTIONS: dict[str, RecoveryActionSpec] = {
    "retry_hard_screening": RecoveryActionSpec(
        "retry_hard_screening", "重新运行硬筛", retry_scope="hard_screening"
    ),
    "review_hard_screening_pass": RecoveryActionSpec(
        "review_hard_screening_pass", "人工通过硬筛", requires_input=True,
        retry_scope="hard_screening_review",
    ),
    "review_hard_screening_reject": RecoveryActionSpec(
        "review_hard_screening_reject", "人工判定硬筛不通过", requires_input=True,
        retry_scope="hard_screening_review",
    ),
    "repair_hard_screening_policy": RecoveryActionSpec(
        "repair_hard_screening_policy", "处理硬筛条件", requires_input=True,
        retry_scope="hard_screening_policy",
    ),
    "run_scoring": RecoveryActionSpec(
        "run_scoring", "重新尝试初步筛选", retry_scope="screening"
    ),
    "retry_initial_assessment": RecoveryActionSpec(
        "retry_initial_assessment", "重试启动初步筛选", retry_scope="initial_assessment"
    ),
    "rebuild_screening_assessment": RecoveryActionSpec(
        "rebuild_screening_assessment", "重新生成初步筛选依据",
        retry_scope="rebuild_screening_assessment",
    ),
    "repair_resume_source": RecoveryActionSpec(
        "repair_resume_source", "重新解析或校正简历", requires_input=True,
        retry_scope="resume_profile",
    ),
    "repair_job_profile": RecoveryActionSpec(
        "repair_job_profile", "处理岗位画像", requires_input=True,
        retry_scope="job_profile",
    ),
    "retry_post_first_scoring": RecoveryActionSpec(
        "retry_post_first_scoring", "从失败处继续计算",
        retry_scope="post_first_scoring",
    ),
    "retry_post_second_scoring": RecoveryActionSpec(
        "retry_post_second_scoring", "从失败处继续计算",
        retry_scope="post_second_scoring",
    ),
    "edit_first_interview_feedback": RecoveryActionSpec(
        "edit_first_interview_feedback", "修改一面记录并重新计算",
        requires_input=True, retry_scope="post_first_scoring",
    ),
    "edit_second_interview_feedback": RecoveryActionSpec(
        "edit_second_interview_feedback", "修改二面记录并重新计算",
        requires_input=True, retry_scope="post_second_scoring",
    ),
    "rebuild_previous_post_first_assessment": RecoveryActionSpec(
        "rebuild_previous_post_first_assessment", "重新生成上一轮评估依据",
        retry_scope="rebuild_post_first_assessment",
    ),
}


def allowed_application_actions(
    db: Session,
    user: User,
    application: Application,
    candidates: list[str],
) -> list[str]:
    """Returns page actions using the same Application policy as command endpoints."""

    allowed: list[str] = []
    authorization = AuthorizationService(db)
    for action in candidates:
        if authorization.can_application_action(user, application, action):
            allowed.append(action)
    return allowed


def application_recovery_actions(
    db: Session,
    user: User,
    application: Application,
    action_codes: list[str],
    *,
    scoring_error_code: str = "",
    application_recovery_code: str = "",
) -> list[dict[str, object]]:
    """Project executable recovery choices for one Application.

    Navigation actions are included only when the user can operate the related
    business area. Command actions use the exact Application authorization rule
    that their endpoints enforce.
    """
    # 已知来源错误必须先改变来源再重跑；继续暴露 run_scoring 只会让用户
    # 重复进入同一个失败分支。来源修复成功后由后端自动启动新的 V1。
    if application_recovery_code in {
        "screening_resume_source_required",
        "screening_job_profile_required",
        "screening_source_review_required",
    }:
        action_codes = [code for code in action_codes if code != "run_scoring"]

    # 终态申请只保留查看和审计入口；旧数据即使残留 recovery_code，
    # 也不能再给已经“不通过/已结束”的申请生成恢复按钮。
    if is_application_closed(application.status):
        return []
    recovery_context = (
        dict(application.recovery_context_json or {})
        if isinstance(application.recovery_context_json, dict)
        else {}
    )
    effective_recovery_code = str(
        application_recovery_code or getattr(application, "recovery_code", "") or ""
    )
    detail_code = str(recovery_context.get("errorCode") or "").casefold()
    if any(marker in detail_code for marker in (
        "record_set_changed", "parse_review_required", "anchor_input_too_large",
    )):
        # 这些错误完全由当前面评输入决定；直接重试不会改变输入，只会再次失败。
        action_codes = [
            code for code in action_codes
            if code not in {"retry_post_first_scoring", "retry_post_second_scoring"}
        ]

    if effective_recovery_code in {
        "post_first_source_review_required", "post_second_source_review_required",
    }:
        action_codes = _source_recovery_action_codes(
            db,
            application,
            action_codes,
            recovery_code=effective_recovery_code,
            error_code=detail_code,
            recovery_context=recovery_context,
        )
    if recovery_context.get("errorCode") in {
        "assessment_first_interview_plan_missing",
        "assessment_first_interview_plan_source_mismatch",
        "assessment_confirmed_first_interview_guide_missing",
        "assessment_confirmed_first_interview_questions_missing",
    }:
        # 新版 V2 把题单视为可选的语义上下文，历史题单错误可直接使用不可变
        # 面评原文重跑；重建 V1 不会修复旧题单，也不应继续误导用户。
        action_codes = [
            code for code in action_codes if code != "rebuild_screening_assessment"
        ]
    authorization = AuthorizationService(db)
    allowed: list[str] = []
    for code in action_codes:
        if code == "repair_resume_source":
            # 该动作会打开并处理申请当前采用的 ResumeSubmission。目标不存在时
            # 不得下发一个前端无法执行的按钮；来源缺失仍通过恢复说明提示用户。
            submission_id = str(application.adopted_resume_submission_id or "")
            if not submission_id or db.get(ResumeSubmission, submission_id) is None:
                continue
            if authorization.can_business_action(
                user, "resume_submission.manage", department_id=application.department_id
            ):
                allowed.append(code)
            continue
        if code in {"repair_job_profile", "repair_hard_screening_policy"}:
            # 岗位画像修复会修改岗位来源；硬筛策略修复则直接进入规则编辑器。
            # 两者必须分别复用目标接口的权限，不能用 job.edit 一概代替。
            permission = (
                "hard_screening.policy.manage"
                if code == "repair_hard_screening_policy"
                else "job.edit"
            )
            if authorization.can_business_action(
                user, permission, department_id=application.department_id
            ):
                allowed.append(code)
            continue
        authorization_action = {
            "rebuild_previous_post_first_assessment": "retry_post_first_scoring",
        }.get(code, code)
        if authorization.can_application_action(user, application, authorization_action):
            allowed.append(code)
    actions = public_recovery_actions(allowed, APPLICATION_RECOVERY_ACTIONS)
    if (
        application_recovery_code == "screening_publish_retryable"
        or scoring_error_code.startswith("publish:")
    ):
        for action in actions:
            if action["action"] == "run_scoring":
                action["label"] = "重新发布初步筛选结果"
                action["retryScope"] = "publish_screening_assessment"
    return actions


def _source_recovery_action_codes(
    db: Session,
    application: Application,
    action_codes: list[str],
    *,
    recovery_code: str,
    error_code: str,
    recovery_context: dict[str, object],
) -> list[str]:
    """将内部来源故障投影成业务动作，并隐藏尚不具备前置条件的重试。"""
    first = recovery_code == "post_first_source_review_required"
    previous_stage = "screening" if first else "after_first_interview"
    retry_action = "retry_post_first_scoring" if first else "retry_post_second_scoring"
    upstream_workflow = "scoring_workflow" if first else "post_first_scoring_workflow"
    actions = list(action_codes)

    if "frozen_resume" in error_code:
        actions.insert(0, "repair_resume_source")
    elif "frozen_job" in error_code:
        actions.insert(0, "repair_job_profile")

    active_upstream = any(
        upstream_workflow != "scoring_workflow"
        or bool((run.input_json or {}).get("rebuild_published_assessment"))
        for run in db.scalars(
            select(WorkflowRun).where(
                WorkflowRun.application_id == application.application_id,
                WorkflowRun.workflow_type == upstream_workflow,
                WorkflowRun.status.in_(("pending", "running")),
            )
        )
    )
    requires_new_source = any(marker in error_code for marker in (
        "previous_version", "frozen_resume", "frozen_job", "topology",
    ))
    source_changed = True
    if requires_new_source and "sourceAssessmentVersionId" in recovery_context:
        latest = db.scalars(
            select(ApplicationAssessmentVersion)
            .where(
                ApplicationAssessmentVersion.application_id == application.application_id,
                ApplicationAssessmentVersion.stage == previous_stage,
            )
            .order_by(
                ApplicationAssessmentVersion.version.desc(),
                ApplicationAssessmentVersion.created_at.desc(),
            )
        ).first()
        source_changed = bool(latest) and str(latest.assessment_version_id) != str(
            recovery_context.get("sourceAssessmentVersionId") or ""
        )
    if active_upstream or not source_changed:
        actions = [code for code in actions if code != retry_action]

    # 保持合同定义的顺序并去重，避免同一恢复动作在多个来源分类中重复展示。
    return list(dict.fromkeys(actions))



