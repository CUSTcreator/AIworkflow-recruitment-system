"""V1 初筛冻结来源的就绪性决策。

本服务只判断“能否以一组不可变来源启动评分”，不调用 LLM、不读写数据库、也不
执行重试。外部异常继续由 ExternalActivity 统一分类；这里把岗位/简历版本缺失、
模型目录和排名数据等预期前置条件显式映射为 Workflow 可持久化的业务结论。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from backend.app.shared.workflows import RecoveryAction, StepErrorCategory
from backend.app.modules.jobs.profile_readiness import evaluate_job_profile_json
from recruitment_ai_core.screening_scoring.resume_experience.preset_models import (
    DEFAULT_MODEL_ID,
    DEFAULT_MODEL_VERSION,
    get_preset_model,
)


class ScreeningSourceReadinessStatus(StrEnum):
    """冻结来源检查的唯一结果类型。"""

    READY = "ready"
    BLOCKED = "blocked"
    FAILED_CONFIGURATION = "failed_configuration"
    FAILED_INTERNAL = "failed_internal"


@dataclass(frozen=True, slots=True)
class ScreeningSourceReadiness:
    """供冻结 Step 映射为 ``StepOutcome`` 的稳定决策。"""

    status: ScreeningSourceReadinessStatus
    error_code: str = ""
    message: str = ""
    error_category: StepErrorCategory | None = None
    recovery_action: RecoveryAction | None = None
    degraded: bool = False

    @property
    def is_ready(self) -> bool:
        return self.status == ScreeningSourceReadinessStatus.READY


class ScreeningSourceReadinessService:
    """验证 Application 的 V1 冻结来源与系统评分配置。

    设计边界：候选人经历为空、没有 WorkUnit 或没有 SkillClaim 都是正常的稀疏
    输入，必须继续评分；只有无法确定来源版本、岗位尚未具备可评分能力，或系统
    数据集/模型目录不可用时才返回非 ready。
    """

    def assess(
        self,
        *,
        application: Any | None,
        job: Any | None,
        resume_profile: Any | None,
        job_profile: Any | None,
        job_version: Any | None,
        ranking_entries: list[dict[str, Any]] | None = None,
        expected_ranking_count: int = 200,
        projected_resume_profile: dict[str, Any] | None = None,
        projected_job_profile: dict[str, Any] | None = None,
        projection_error: Exception | None = None,
        allow_downstream_rebuild: bool = False,
    ) -> ScreeningSourceReadiness:
        """按固定顺序检查来源完整性、投影完整性和部署配置。"""
        if application is None:
            return self._internal("screening_application_missing", "评分申请不存在或已被删除。")
        status = str(getattr(application, "status", "") or "")
        # 常规 V1 只能在初筛阶段冻结。来源重建会创建新的不可变 V1，
        # 但必须保留已到达的 V2/V3 审核阶段；该受控例外只由带显式 run 标记
        # 的工作流传入，不能放宽普通初筛请求的状态机约束。
        allowed_statuses = {"screening_running"}
        if allow_downstream_rebuild:
            allowed_statuses.update({"hr_second_review", "final_review"})
        if status not in allowed_statuses:
            return self._internal("screening_application_state_invalid", "申请当前不处于初步筛选评分状态。")
        if job is None or not getattr(application, "job_id", None):
            return self._internal("screening_application_job_missing", "申请未绑定有效岗位。")

        if resume_profile is None:
            return self._blocked(
                "application_frozen_resume_profile_missing",
                "简历画像尚未就绪，请完成简历解析后重试初步筛选。",
            )
        if getattr(resume_profile, "candidate_id", None) != getattr(application, "candidate_id", None):
            return self._internal(
                "screening_resume_profile_candidate_mismatch",
                "冻结简历画像与申请候选人不一致。",
            )

        if job_profile is None:
            return self._blocked(
                "application_frozen_job_profile_missing",
                "岗位画像尚未就绪，请完成岗位画像生成后重试初步筛选。",
            )
        if (
            getattr(job_profile, "job_id", None) != getattr(application, "job_id", None)
            or getattr(job_profile, "jd_version_id", None) != getattr(application, "jd_version_id", None)
        ):
            return self._internal(
                "screening_job_profile_binding_mismatch",
                "冻结岗位画像与申请岗位版本不一致。",
            )
        if job_version is None:
            return self._internal(
                "application_frozen_job_version_missing",
                "申请绑定的岗位版本不存在。",
            )
        if (
            getattr(job_version, "job_id", None) != getattr(application, "job_id", None)
            or getattr(job_version, "jd_version_id", None) != getattr(application, "jd_version_id", None)
        ):
            return self._internal(
                "screening_job_version_binding_mismatch",
                "冻结岗位版本与申请岗位不一致。",
            )

        if projection_error is not None:
            return self._internal(
                "screening_source_projection_invalid",
                f"冻结评分来源投影失败：{type(projection_error).__name__}。",
            )
        # 调用方在第二次检查时传入投影和排名数据；首次对象关系检查无需等待它们。
        if projected_resume_profile is None or projected_job_profile is None:
            return ScreeningSourceReadiness(ScreeningSourceReadinessStatus.READY)

        model_id = str(projected_job_profile.get("preset_model_id") or "")
        model_version = str(projected_job_profile.get("preset_model_version") or "")
        model_fallback_message = ""
        try:
            get_preset_model(model_id, model_version)
        except (TypeError, ValueError):
            # 评分模型是算法配置，不是用户需要理解或编辑的业务来源。
            # 失效配置只要系统内置默认模型仍可用，就在冻结边界替换为默认值；
            # 这样不会把一个可评分的申请卡在“修改模型”按钮上。若默认模型也
            # 不存在，才保留配置失败，让用户重试等待部署恢复。
            try:
                default_model = get_preset_model(DEFAULT_MODEL_ID, DEFAULT_MODEL_VERSION)
            except (TypeError, ValueError):
                return self._configuration(
                    "screening_preset_model_unavailable",
                    "岗位绑定的能力模型或版本在当前部署中不可用。",
                )
            projected_job_profile["preset_model_id"] = default_model["model_id"]
            projected_job_profile["preset_model_version"] = default_model["version"]
            model_id = str(default_model["model_id"])
            model_version = str(default_model["version"])
            model_fallback_message = (
                f"岗位评分模型配置不可用，已自动使用系统默认模型 {model_id}/{model_version}。"
            )

        # “记录存在”不是可评分条件。这里与岗位路由、Application 创建共用同一
        # 内容校验，继续保留最后一道防线以应对历史脏数据和并发版本切换。
        profile_readiness = evaluate_job_profile_json(projected_job_profile)
        if not profile_readiness.ready:
            missing = ", ".join(profile_readiness.missing_required_unit_ids)
            return self._blocked(
                profile_readiness.error_code,
                (
                    f"岗位能力画像未覆盖职责单元：{missing}，请重新生成岗位画像。"
                    if missing
                    else "岗位尚未生成可评分能力，请完成岗位配置或重新生成岗位画像。"
                ),
            )

        if ranking_entries is not None and len(ranking_entries) != expected_ranking_count:
            # education_scoring 已定义无排名/部分排名的保守基准分。排名数据少量
            # 缺失不会影响经历和能力评分，继续执行并在冻结结果中标记降级即可。
            return ScreeningSourceReadiness(
                ScreeningSourceReadinessStatus.READY,
                error_code="education_ranking_dataset_incomplete",
                message="；".join(
                    item for item in (
                        model_fallback_message,
                        f"院校排名数据不完整（期望 {expected_ranking_count} 条，实际 {len(ranking_entries)} 条），"
                        "学历分将按可用数据和保守基准计算。",
                    ) if item
                ),
                error_category=StepErrorCategory.INFRASTRUCTURE,
                recovery_action=RecoveryAction.USER_RETRY,
                degraded=True,
            )

        if model_fallback_message:
            return self._degraded("screening_preset_model_fallback", model_fallback_message)
        return ScreeningSourceReadiness(ScreeningSourceReadinessStatus.READY)

    @staticmethod
    def _blocked(code: str, message: str) -> ScreeningSourceReadiness:
        return ScreeningSourceReadiness(
            ScreeningSourceReadinessStatus.BLOCKED,
            error_code=code,
            message=message,
            error_category=StepErrorCategory.BUSINESS_RULE,
            recovery_action=RecoveryAction.USER_RETRY,
        )

    @staticmethod
    def _configuration(code: str, message: str) -> ScreeningSourceReadiness:
        return ScreeningSourceReadiness(
            ScreeningSourceReadinessStatus.FAILED_CONFIGURATION,
            error_code=code,
            message=message,
            error_category=StepErrorCategory.INFRASTRUCTURE,
            # 重新发起评分会重新读取当前配置和数据集；一次任务的配置问题
            # 不应把用户永久卡在“联系管理员”。
            recovery_action=RecoveryAction.USER_RETRY,
        )

    @staticmethod
    def _degraded(code: str, message: str) -> ScreeningSourceReadiness:
        """来源仍可评分时，仅记录降级提示，不把申请转成失败。"""
        return ScreeningSourceReadiness(
            ScreeningSourceReadinessStatus.READY,
            error_code=code,
            message=message,
            error_category=StepErrorCategory.INFRASTRUCTURE,
            recovery_action=RecoveryAction.USER_RETRY,
            degraded=True,
        )

    @staticmethod
    def _internal(code: str, message: str) -> ScreeningSourceReadiness:
        return ScreeningSourceReadiness(
            ScreeningSourceReadinessStatus.FAILED_INTERNAL,
            error_code=code,
            message=message,
            error_category=StepErrorCategory.INTERNAL,
            # 先通过重新冻结/重新评分自愈；若来源仍不一致，页面仍可引导用户
            # 回到简历解析或岗位画像处理对应来源。
            recovery_action=RecoveryAction.USER_RETRY,
        )
