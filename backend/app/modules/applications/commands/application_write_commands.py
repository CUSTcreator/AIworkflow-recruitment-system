"""Application 写命令编排：执行人工决策和生命周期写操作。"""

from typing import Any, Callable

from sqlalchemy.orm import Session

from sqlalchemy import select

from backend.app.models.entities import Application, JobRequirementProfileRecord, JobVersionRecord, User
from backend.app.modules.applications.commands.application_command_executor import ApplicationCommandExecutor
from backend.app.modules.applications.domain.application_navigation_policy import application_main_route
from backend.app.modules.applications.services.application_access_service import ApplicationAccessService
from backend.app.modules.applications.commands.application_decision_commands import ApplicationDecisionCommands
from backend.app.modules.applications.commands.application_lifecycle_commands import ApplicationLifecycleCommands
from backend.app.modules.applications.contracts import retry_initial_assessment_for_application
from backend.app.modules.applications.application_recovery import (
    ApplicationRecoveryCode,
    set_application_recovery,
)
from backend.app.shared.errors import BusinessError


class ApplicationWriteCommands:
    def __init__(self, db: Session) -> None:
        self.db = db

    @staticmethod
    def payload(body: Any | None) -> dict[str, Any]:
        return body.model_dump(exclude_unset=True, mode="json") if body is not None else {}

    def execute(
        self, *, user: User, idempotency_key: str, application_id: str, action: str,
        body: dict[str, Any], handler: Callable[..., dict[str, Any]], authorization_action: str | None = None,
        resource_loader=None,
    ) -> dict[str, Any]:
        """通过 CommandRunner 兼容适配器执行 Application 写命令。"""
        response = ApplicationCommandExecutor(self.db).execute(
            user=user, idempotency_key=idempotency_key, action=action,
            application_id=application_id, body=body, handler=handler,
            authorization_action=authorization_action, resource_loader=resource_loader,
        )
        response.setdefault("next_route", application_main_route(str(response.get("application_id") or application_id), str(response.get("status") or "")))
        return response

    def delete(
        self,
        *,
        user: User,
        application_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """软删除也必须经过统一授权、幂等与事务边界。"""

        def load_including_deleted(db: Session, identifier: str) -> Application | None:
            return (
                db.query(Application)
                .filter(Application.application_id == identifier)
                .with_for_update()
                .one_or_none()
            )

        return self.execute(
            user=user,
            idempotency_key=idempotency_key,
            application_id=application_id,
            action="application.delete",
            authorization_action="delete_application",
            body={},
            resource_loader=load_including_deleted,
            handler=lambda actor, _application, _body: ApplicationLifecycleCommands(
                self.db
            ).soft_delete(actor, application_id),
        )

    def retry_initial_assessment(
        self,
        *,
        user: User,
        application_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """从 Application 与初筛调度交汇点恢复，不重复处理上游材料。"""

        def handler(actor: User, application: Application, _body: dict[str, Any]) -> dict[str, Any]:
            workflow_run_id = retry_initial_assessment_for_application(
                self.db,
                application=application,
                actor=actor,
                source_key=f"manual-initial-assessment:{idempotency_key}",
            )
            return {
                "application_id": application.application_id,
                "status": application.status,
                "message": "初步筛选任务已重新启动",
                "workflow_run_id": workflow_run_id,
                "run_status": "pending",
            }

        return self.execute(
            user=user,
            idempotency_key=idempotency_key,
            application_id=application_id,
            action="retry_initial_assessment",
            body={},
            handler=handler,
        )

    def recover_job_profile(
        self,
        *,
        user: User,
        application_id: str,
        idempotency_key: str,
        mode: str,
    ) -> dict[str, Any]:
        """重建该投递真正冻结的岗位来源，并在画像就绪后自动恢复 V1。"""

        def handler(actor: User, application: Application, body: dict[str, Any]) -> dict[str, Any]:
            if application.status != "screening_failed":
                raise BusinessError(
                    "job_profile_recovery_state_invalid",
                    "当前申请不处于需要修复岗位来源的状态",
                    status_code=409,
                )
            if str(application.recovery_code or "") not in {
                ApplicationRecoveryCode.SCREENING_JOB_PROFILE_REQUIRED.value,
                ApplicationRecoveryCode.SCREENING_SOURCE_REVIEW_REQUIRED.value,
                ApplicationRecoveryCode.SCREENING_MODEL_CONFIGURATION_REQUIRED.value,
            }:
                raise BusinessError(
                    "job_profile_recovery_reason_invalid",
                    "当前初步筛选异常不需要重建岗位来源",
                    status_code=409,
                )

            versions = list(self.db.scalars(
                select(JobVersionRecord)
                .where(JobVersionRecord.job_id == application.job_id)
                .order_by(JobVersionRecord.version.desc())
            ))
            requested_mode = str(body.get("mode") or "reprocess_frozen")
            if requested_mode == "adopt_current":
                target = versions[0] if versions else None
            else:
                target = next(
                    (item for item in versions if item.jd_version_id == application.jd_version_id),
                    None,
                )
            if target is None:
                message = (
                    "当前岗位没有可采用的岗位版本"
                    if requested_mode == "adopt_current"
                    else "该投递冻结的岗位版本已不可用，请选择采用当前岗位要求"
                )
                raise BusinessError("job_profile_recovery_version_missing", message, status_code=409)

            application.jd_version_id = target.jd_version_id
            profile = (
                self.db.get(JobRequirementProfileRecord, target.active_job_profile_id)
                if target.active_job_profile_id else None
            )
            from backend.app.modules.jobs.profile_readiness import evaluate_job_profile_record
            from backend.app.modules.assessment.services.assessment_scheduling_service import (
                enqueue_screening_retry_after_job_profile,
            )
            from backend.app.modules.jobs.services.job_profile_service import JobProfileService

            ready = evaluate_job_profile_record(
                profile,
                job_id=application.job_id,
                jd_version_id=target.jd_version_id,
            ).ready
            if ready and requested_mode == "adopt_current":
                application.job_profile_id = profile.job_profile_id
                run = enqueue_screening_retry_after_job_profile(
                    self.db,
                    user=actor,
                    application=application,
                    source_key=f"application-job-source:{idempotency_key}",
                )
                return {
                    "application_id": application.application_id,
                    "status": application.status,
                    "message": "已采用当前岗位要求并重新启动初步筛选",
                    "workflow_run_id": run.workflow_run_id,
                    "workflow_type": run.workflow_type,
                    "run_status": "pending",
                }

            # 即使已有画像，“重新处理冻结版本”也必须创建新 revision，不能复用
            # 可能正是本次异常来源的旧 JobCapability。模型配置失效时，画像编译
            # 和后续 V1 都使用系统内置默认模型，用户无需接触算法配置。
            from recruitment_ai_core.screening_scoring.resume_experience.preset_models import (
                DEFAULT_MODEL_ID,
                DEFAULT_MODEL_VERSION,
                get_preset_model,
            )
            model_id = str(target.preset_model_id or "")
            model_version = str(target.preset_model_version or "")
            try:
                get_preset_model(model_id, model_version)
            except (TypeError, ValueError):
                model_id = DEFAULT_MODEL_ID
                model_version = DEFAULT_MODEL_VERSION
            application.job_profile_id = None
            run = JobProfileService(self.db).enqueue_for_version(
                target,
                triggered_by=actor.user_id,
                force=True,
                preset_model_id=model_id,
                preset_model_version=model_version,
            )
            if run is None:
                raise RuntimeError("job_profile_recovery_run_not_created")
            set_application_recovery(
                application,
                ApplicationRecoveryCode.SCREENING_JOB_PROFILE_REQUIRED,
                context={
                    "jobProfileRecoveryRunId": run.workflow_run_id,
                    "jdVersionId": target.jd_version_id,
                    "mode": requested_mode,
                },
            )
            return {
                "application_id": application.application_id,
                "status": application.status,
                "message": "岗位来源正在重新处理，完成后会自动重新进行初步筛选",
                "workflow_run_id": run.workflow_run_id,
                "workflow_type": run.workflow_type,
                "run_status": "pending",
            }

        return self.execute(
            user=user,
            idempotency_key=idempotency_key,
            application_id=application_id,
            action="repair_job_profile",
            authorization_action="repair_job_profile",
            body={"mode": mode},
            handler=handler,
        )

    def execute_decision(self, *, user: User, idempotency_key: str, application_id: str, action: str, body: dict[str, Any]) -> dict[str, Any]:
        """先解析请求体中的真实领域动作，再进入统一命令授权边界。"""
        handlers = {
            "final_decision": ApplicationDecisionCommands(self.db).final_decision,
            "department_decision": ApplicationDecisionCommands(self.db).department_decision,
            "hr_decision": ApplicationDecisionCommands(self.db).hr_decision,
        }
        authorization_action = self._decision_authorization_action(action, body)
        return self.execute(
            user=user, idempotency_key=idempotency_key, application_id=application_id,
            action=action, body=body, handler=handlers[action],
            authorization_action=authorization_action,
        )

    @staticmethod
    def _decision_authorization_action(command: str, body: dict[str, Any]) -> str:
        """校验阶段决策输入，并返回该阶段统一的接口授权动作。

        请求体中的 reject/hold/manual_review 只决定状态迁移，不决定权限；
        同一阶段的所有决策统一使用该阶段已有的 manage 原子权限。
        """
        decision = body.get("decision")
        choices = {
            "final_decision": {"offer_process": "offer", "closed_rejected": "reject", "manual_review": "manual_review"},
            "department_decision": {"暂缓": "hold", "不推进": "reject", "人工复核": "manual_review"},
            "hr_decision": {"暂缓": "hold", "不推进": "reject", "补充验证": "manual_review"},
        }
        state_action = choices.get(command, {}).get(decision)
        if state_action is None:
            errors = {
                "final_decision": ("final_decision_invalid", "无效的最终决策"),
                "department_decision": ("department_decision_invalid", "无效的部门决策"),
                "hr_decision": ("hr_decision_invalid", "无效的 HR 决策"),
            }
            code, detail = errors.get(command, ("application_decision_invalid", "无效的申请决策"))
            # 在进入统一授权边界前保留原领域命令的输入校验契约，避免无效
            # decision 被解释为权限错误。
            raise BusinessError(code, detail)
        return command








