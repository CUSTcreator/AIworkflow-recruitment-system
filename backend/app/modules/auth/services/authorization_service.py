"""统一授权应用服务。

Router、CommandRunner 和 QueryService 应调用本服务；它负责将数据库中的 User / Application
转换为领域访问上下文，再调用纯策略并在拒绝时抛出一致的 BusinessError。
"""
from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from backend.app.models.entities import Application, Candidate, Job, ResumeSubmission, User
from backend.app.modules.auth.domain.access_context import AccessContext, ApplicationAccessContext
from backend.app.modules.auth.domain.authorization_policy import (
    can_application_action,
    can_business_action,
    can_view_application,
    can_view_application_material,
    scope_allows,
)
from backend.app.modules.auth.domain.permission_catalog import (
    PERMISSION_LABELS,
    RESUME_SUBMISSION_ACTION_REQUIREMENTS,
    application_action_requirement,
)
from backend.app.modules.auth.services.permission_resolution_service import PermissionResolutionService
from backend.app.shared.errors import BusinessError


class AuthorizationService:
    """授权层的唯一写/读执行入口；不提交业务事务。

    Router、CommandRunner 和 QueryService 必须通过本服务执行后端鉴权；前端仅可
    用 ``can_*`` 结果投影按钮，不能替代这里的资源范围、职责或负责人校验。
    """

    def __init__(self, db: Session) -> None:
        self.db = db
        self.permissions = PermissionResolutionService(db)

    def access_context(self, user: User) -> AccessContext:
        """把 ORM User 固化为当前请求可复用的授权快照。"""
        return AccessContext(
            user_id=user.user_id,
            is_active=bool(user.is_active and getattr(user, "deleted_at", None) is None),
            business_scope=user.business_scope,
            department_id=user.department_id,
            permissions=frozenset(self.permissions.effective_permissions(user)),
            is_system_admin=user.is_system_admin,
        )

    @staticmethod
    def application_context(application: Application) -> ApplicationAccessContext:
        """提取 Application 授权判断所需字段，不泄露整个 ORM 对象给策略层。"""
        return ApplicationAccessContext(
            application_id=application.application_id,
            department_id=application.department_id,
            assigned_first_interviewer=application.assigned_first_interviewer,
            assigned_hr=application.assigned_hr,
            status=application.status,
        )

    def can_business_action(self, user: User, permission_code: str, **kwargs: object) -> bool:
        """返回通用业务权限结论，适合页面动作投影等非异常场景。"""
        return can_business_action(self.access_context(user), permission_code, **kwargs).allowed

    def can_view_data_in_department(self, user: User, department_id: str | None) -> bool:
        """判断普通业务数据是否可见，不把查看能力编码为职责原子权限。

        调用方已知资源归属部门而不需要 Application 级负责人限制时，应使用该
        方法。它与 ``can_view_application`` 共用同一公式：账号有效 + 范围覆盖。
        """
        context = self.access_context(user)
        return context.is_active and scope_allows(context, department_id)

    def require_data_view(self, user: User) -> None:
        """要求账号处于有效状态，供列表、统计等尚未定位具体部门的读取使用。"""
        if self.access_context(user).is_active:
            return
        raise BusinessError("account_inactive", "当前账号已停用", status_code=403)

    def require_business_action(self, user: User, permission_code: str, **kwargs: object) -> None:
        """要求通用权限与数据范围；拒绝时统一抛出 403。"""
        if permission_code not in PERMISSION_LABELS:
            raise RuntimeError(f"unknown_permission:{permission_code}")
        decision = can_business_action(self.access_context(user), permission_code, **kwargs)
        if not decision.allowed:
            raise BusinessError("permission_denied", f"无权执行操作：{PERMISSION_LABELS[permission_code]}", status_code=403, context={"reason": decision.reason_code})

    @staticmethod
    def require_system_admin(user: User) -> None:
        """系统管理只由账号管理员标记和组织范围决定。"""
        if not user.is_system_admin or user.business_scope != "organization":
            raise BusinessError(
                "system_admin_required",
                "需要组织范围的系统管理员身份",
                status_code=403,
            )

    def can_application_action(self, user: User, application: Application, action: str) -> bool:
        """返回 Application 动作结论，覆盖权限、部门范围与负责人限制。"""
        return can_application_action(self.access_context(user), self.application_context(application), action).allowed

    def can_view_application(self, user: User, application: Application) -> bool:
        """返回与 ApplicationAccessService 完全一致的申请可见性结论。"""
        return can_view_application(
            self.access_context(user), self.application_context(application)
        ).allowed

    def require_application_view(self, user: User, application: Application) -> None:
        """要求申请基础可见性，供读取和纯恢复动作共用。"""
        decision = can_view_application(
            self.access_context(user), self.application_context(application)
        )
        if decision.allowed:
            return
        messages = {
            "account_inactive": "当前账号已停用",
            "department_scope_forbidden": "候选人不在当前账号的数据范围内",
            "application_view_forbidden": "无权查看该候选申请",
        }
        codes = {
            "department_scope_forbidden": "candidate_scope_forbidden",
            "application_view_forbidden": "application_view_forbidden",
        }
        raise BusinessError(
            codes.get(decision.reason_code, "application_view_forbidden"),
            messages.get(decision.reason_code, "无权查看该候选申请"),
            status_code=403,
            context={"reason": decision.reason_code},
        )

    def require_application_material_view(self, user: User, application: Application) -> None:
        """要求完整候选材料访问权，避免原始文件只靠前端隐藏。"""
        self.require_application_view(user, application)
        decision = can_view_application_material(
            self.access_context(user), self.application_context(application)
        )
        if decision.allowed:
            return
        raise BusinessError(
            "candidate_detail_forbidden",
            "无权查看候选人完整资料",
            status_code=403,
            context={"reason": decision.reason_code},
        )

    def require_application_action(self, user: User, application: Application, action: str) -> None:
        """要求 Application 动作授权；保留现有稳定错误码，便于渐进迁移。"""
        decision = can_application_action(self.access_context(user), self.application_context(application), action)
        if decision.allowed:
            return
        messages = {
            "permission_not_defined": f"未定义操作权限：{action}",
            "first_interviewer_not_assigned": "当前账号未被分配到该候选人的技术一面",
            "hr_not_assigned": "当前账号未被分配到该候选人的 HR 环节",
        }
        requirement = application_action_requirement(
            action,
            application_status=application.status,
        )
        generic_message = (
            f"无权执行操作：{PERMISSION_LABELS[requirement.permission_code]}"
            if requirement is not None and requirement.permission_code is not None
            else "无权执行当前操作"
        )
        raise BusinessError(
            decision.reason_code if decision.reason_code in messages else "permission_denied",
            messages.get(decision.reason_code, generic_message),
            status_code=403,
            context={"action": action, "reason": decision.reason_code},
        )

    def _resume_submission_applications(
        self, submission: ResumeSubmission
    ) -> list[Application]:
        predicates = []
        if submission.application_id:
            predicates.append(Application.application_id == submission.application_id)
        if submission.candidate_id:
            predicates.append(Application.candidate_id == submission.candidate_id)
        if not predicates:
            return []
        return list(
            self.db.scalars(
                select(Application).where(
                    Application.deleted_at.is_(None), or_(*predicates)
                )
            )
        )

    def can_view_resume_submission(
        self, user: User, submission: ResumeSubmission
    ) -> bool:
        """判断简历处理记录的基础可见性。"""
        context = self.access_context(user)
        if not context.is_active:
            return False
        applications = self._resume_submission_applications(submission)
        if applications:
            return any(
                can_view_application(context, self.application_context(application)).allowed
                for application in applications
            )
        if submission.uploaded_by == user.user_id:
            return True
        if submission.job_id:
            job = self.db.get(Job, submission.job_id)
            if job is not None:
                return scope_allows(context, job.department_id)
        return context.business_scope == "organization"

    def require_resume_submission_view(
        self, user: User, submission: ResumeSubmission
    ) -> None:
        if self.can_view_resume_submission(user, submission):
            return
        raise BusinessError(
            "resume_submission_view_forbidden",
            "无权查看该简历处理记录",
            status_code=403,
            context={"resource": "resume_submission"},
        )

    def can_view_resume_submission_material(
        self, user: User, submission: ResumeSubmission
    ) -> bool:
        """完整简历材料随简历处理记录的可见性继承。"""
        return self.can_view_resume_submission(user, submission)

    def require_resume_submission_material_view(
        self, user: User, submission: ResumeSubmission
    ) -> None:
        self.require_resume_submission_view(user, submission)
        if self.can_view_resume_submission_material(user, submission):
            return
        raise BusinessError(
            "candidate_detail_forbidden",
            "无权查看候选人完整资料",
            status_code=403,
            context={"resource": "resume_submission"},
        )

    def require_candidate_material_scope(self, user: User, candidate_id: str) -> None:
        """要求能完整访问候选人的每一条有效申请，供跨记录写操作使用。"""
        if self.can_candidate_material_scope(user, candidate_id):
            return
        raise BusinessError(
            "candidate_scope_forbidden",
            "无权修改该候选人在其他部门的申请资料",
            status_code=403,
            context={"candidateId": candidate_id},
        )

    def can_candidate_material_scope(self, user: User, candidate_id: str) -> bool:
        """判断是否覆盖候选人的全部有效申请，供跨部门写操作使用。"""
        context = self.access_context(user)
        applications = list(
            self.db.scalars(
                select(Application).where(
                    Application.candidate_id == candidate_id,
                    Application.deleted_at.is_(None),
                )
            )
        )
        if applications and all(
            can_view_application_material(
                context, self.application_context(application)
            ).allowed
            for application in applications
        ):
            return True
        if not applications and context.is_active and context.business_scope == "organization":
            return True
        return False

    def can_candidate_material_view(self, user: User, candidate_id: str) -> bool:
        """判断候选人资料是否可读。

        已产生申请时，任一有效申请可见即可读取候选人资料；尚未产生申请时，
        复用 Candidate 当前简历处理记录的可见性，使上传人或目标岗位范围内的
        部门账号仍能处理资料。Candidate 没有可推导范围的历史孤立记录仅允许
        组织范围查看。
        """
        context = self.access_context(user)
        applications = list(
            self.db.scalars(
                select(Application).where(
                    Application.candidate_id == candidate_id,
                    Application.deleted_at.is_(None),
                )
            )
        )
        if applications:
            return any(
                can_view_application_material(
                    context, self.application_context(application)
                ).allowed
                for application in applications
            )
        candidate = self.db.get(Candidate, candidate_id)
        submission = (
            self.db.get(ResumeSubmission, candidate.current_resume_submission_id)
            if candidate is not None and candidate.current_resume_submission_id
            else None
        )
        if submission is not None:
            return self.can_view_resume_submission(user, submission)
        return bool(context.is_active and context.business_scope == "organization")

    def require_candidate_material_view(self, user: User, candidate_id: str) -> None:
        """要求候选人资料可读；不要求用户能看到该候选人的所有申请。"""
        if self.can_candidate_material_view(user, candidate_id):
            return
        raise BusinessError(
            "candidate_scope_forbidden",
            "无权查看该候选人资料",
            status_code=403,
            context={"candidateId": candidate_id},
        )

    def can_resume_submission_action(
        self,
        user: User,
        submission: ResumeSubmission,
        action: str,
    ) -> bool:
        """按简历操作分类给出命令与页面动作的统一结论。"""
        requirement = RESUME_SUBMISSION_ACTION_REQUIREMENTS.get(action)
        if requirement is None:
            return False
        if requirement.authorization_mode == "basic_view":
            return self.can_view_resume_submission(user, submission)
        if requirement.authorization_mode == "material_view":
            return self.can_view_resume_submission_material(user, submission)
        if requirement.authorization_mode == "recovery":
            # 纯自动链路重试不提交人工结论，也不授予其他部门申请的读取权。
            # 简历处理记录本身可见即可执行，不能因候选人另有跨部门申请而隐藏。
            return self.can_view_resume_submission_material(user, submission)
        if requirement.permission_code is None:
            raise RuntimeError(f"resume_submission_permission_missing:{action}")
        context = self.access_context(user)
        if not self.can_view_resume_submission_material(user, submission):
            return False
        # 敏感简历操作按“记录可见 + 对应职责”授权。人工选岗的岗位列表会继续
        # 按数据范围过滤；真正替换已有候选人当前简历的跨记录写操作还会在
        # ResumeReplacementService 中执行严格的全申请范围校验。
        return can_business_action(context, requirement.permission_code).allowed

    def require_resume_submission_action(
        self, user: User, submission: ResumeSubmission, action: str
    ) -> None:
        """要求简历操作授权；命令和页面动作必须调用同一入口。"""
        if action not in RESUME_SUBMISSION_ACTION_REQUIREMENTS:
            raise RuntimeError(f"resume_submission_action_not_defined:{action}")
        if self.can_resume_submission_action(user, submission, action):
            return
        raise BusinessError(
            "resume_submission_action_forbidden",
            "无权执行当前简历处理操作",
            status_code=403,
            context={"resource": "resume_submission", "action": action},
        )
