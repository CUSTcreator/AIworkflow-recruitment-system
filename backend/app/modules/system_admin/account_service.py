from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from backend.app.shared.errors import BusinessRuleError
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from backend.app.core.security import hash_password
from backend.app.models.entities import (
    Application,
    AuditEvent,
    Department,
    Job,
    ResumeSubmission,
    RoleDefinition,
    SourceDocument,
    User,
    UserPermissionOverride,
    WorkflowRun,
)
from backend.app.modules.auth.public import (
    PERMISSION_LABELS,
    ROLE_DEFAULT_PERMISSIONS,
    effective_permissions,
    permission_overrides,
)
from backend.app.modules.auth.domain.permission_catalog import (
    PERMISSION_REQUIRED_BUSINESS_SCOPE,
    RESPONSIBILITY_BUNDLES,
    canonical_responsibility_bundle_code,
    canonical_responsibility_bundle_codes,
    responsibility_bundle_codes_for_permissions,
    role_permissions_for_bundles,
)
from backend.app.shared.audit import record_audit_event
from backend.app.shared.time_serialization import utc_iso
from backend.app.modules.system_admin.repository import SystemAdminRepository


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20].upper()}"


def _iso(value: datetime | None) -> str | None:
    return utc_iso(value) if value else None


class AccountAdminService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repository = SystemAdminRepository(db)

    def list_users(self, keyword: str = "") -> list[dict[str, Any]]:
        query = select(User).order_by(User.deleted_at.is_not(None), User.is_active.desc(), User.display_name, User.user_id)
        if keyword.strip():
            term = f"%{keyword.strip()}%"
            query = query.where(
                or_(
                    User.username.ilike(term),
                    User.display_name.ilike(term),
                    User.user_id.ilike(term),
                )
            )
        departments = {
            item.department_id: item.name
            for item in self.db.scalars(select(Department))
        }
        user_names = {item.user_id: item.display_name for item in self.db.scalars(select(User))}
        return [
            {
                **self._user_view(user),
                "departmentName": departments.get(user.department_id or "", ""),
                "deletedByName": user_names.get(user.deleted_by_user_id or "", ""),
            }
            for user in self.db.scalars(query)
        ]

    def list_roles(self) -> list[dict[str, Any]]:
        return [
            self._role_view(role)
            for role in self.db.scalars(
                select(RoleDefinition).order_by(
                    RoleDefinition.is_active.desc(),
                    RoleDefinition.is_system.desc(),
                    RoleDefinition.name,
                )
            )
        ]

    def create_role(self, actor: User, data: dict[str, Any], *, commit: bool = True) -> dict[str, Any]:
        name = str(data["name"]).strip()
        if self.db.scalar(select(RoleDefinition).where(func.lower(RoleDefinition.name) == name.lower())):
            raise BusinessRuleError(status_code=409, detail="角色名称已存在")
        business_scope = str(data["businessScope"])
        permissions = self._role_permissions_from_payload(
            data, business_scope=business_scope,
        )
        role = RoleDefinition(
            role_id=_id("ROLE"),
            name=name,
            business_scope=business_scope,
            permissions={code: True for code in permissions},
            is_system=False,
            is_active=True,
            created_at=datetime.now(UTC).replace(tzinfo=None),
            updated_at=datetime.now(UTC).replace(tzinfo=None),
        )
        self.db.add(role)
        record_audit_event(
            self.db, actor=actor, action="admin.role.create", target_type="role",
            target_id=role.role_id, summary=f"创建角色：{name}",
        )
        self._finish(commit)
        return self._role_view(role)

    def update_role(self, actor: User, role_id: str, changes: dict[str, Any], *, commit: bool = True) -> dict[str, Any]:
        role = self.repository.role(role_id)
        if role is None:
            raise BusinessRuleError(status_code=404, detail="角色不存在")
        before = self._role_view(role)
        user_count = before["userCount"]
        if user_count and changes.get("isActive") is False:
            raise BusinessRuleError(
                status_code=409,
                detail="该角色仍有账号使用，不能停用",
            )
        next_business_scope = (
            str(changes["businessScope"])
            if changes.get("businessScope") is not None
            else role.business_scope
        )
        scope_changed = next_business_scope != role.business_scope
        assigned_users = list(self.db.scalars(
            select(User).where(User.role_definition_id == role.role_id)
        )) if scope_changed else []
        if scope_changed:
            self._validate_role_scope_change(next_business_scope, assigned_users)
        if changes.get("name") is not None:
            name = str(changes["name"]).strip()
            duplicate = self.db.scalar(
                select(RoleDefinition).where(
                    func.lower(RoleDefinition.name) == name.lower(),
                    RoleDefinition.role_id != role_id,
                )
            )
            if duplicate:
                raise BusinessRuleError(status_code=409, detail="角色名称已存在")
            role.name = name
        if not role.is_system and changes.get("isActive") is not None:
            role.is_active = bool(changes["isActive"])
        elif role.is_system and changes.get("isActive") is False:
            raise BusinessRuleError(status_code=409, detail="内置角色不能停用")
        if "responsibilityBundles" in changes or changes.get("permissions") is not None:
            permissions = self._role_permissions_from_payload(
                changes, business_scope=next_business_scope,
            )
            role.permissions = {code: True for code in permissions}
        else:
            self._validate_permission_scope(
                next_business_scope,
                [
                    code
                    for code, enabled in (role.permissions or {}).items()
                    if enabled and code in PERMISSION_LABELS
                ],
            )
        if scope_changed:
            role.business_scope = next_business_scope
            # 授权运行时读取 User.business_scope。角色范围调整必须在同一事务中
            # 同步全部绑定账号，否则角色配置与实际数据可见范围会短暂不一致。
            for user in assigned_users:
                user.business_scope = next_business_scope
        role.updated_at = datetime.now(UTC).replace(tzinfo=None)
        record_audit_event(
            self.db, actor=actor, action="admin.role.update", target_type="role",
            target_id=role.role_id, summary=f"修改角色：{role.name}",
            details={
                "before": before,
                "after": self._role_view(role),
                "businessScopeChangedUserCount": len(assigned_users) if scope_changed else 0,
            },
        )
        self._finish(commit)
        return self._role_view(role)

    def delete_role(self, actor: User, role_id: str, *, commit: bool = True) -> dict[str, Any]:
        role = self.repository.role(role_id)
        if role is None:
            raise BusinessRuleError(status_code=404, detail="角色不存在")
        if role.is_system:
            raise BusinessRuleError(status_code=409, detail="内置角色不能删除")
        user_count = self.db.scalar(
            select(func.count()).select_from(User).where(User.role_definition_id == role_id)
        ) or 0
        if user_count:
            raise BusinessRuleError(status_code=409, detail="该角色仍有账号使用，请先调整账号角色")
        name = role.name
        self.db.delete(role)
        record_audit_event(
            self.db, actor=actor, action="admin.role.delete", target_type="role",
            target_id=role_id, summary=f"删除角色：{name}",
        )
        self._finish(commit)
        return {"roleId": role_id, "deleted": True}

    def create_user(self, actor: User, data: dict[str, Any], *, commit: bool = True) -> dict[str, Any]:
        username = str(data["username"]).strip().lower()
        display_name = str(data["displayName"]).strip()
        role_definition = self._resolve_role(data.get("roleId"))
        role = role_definition.role_id
        department_id = data.get("departmentId")
        self._validate_user_assignment(role, department_id)
        is_system_admin = bool(data.get("isSystemAdmin"))
        self._validate_system_admin_assignment(role_definition, is_system_admin)
        if self.db.scalar(select(User).where(func.lower(User.username) == username.lower())):
            raise BusinessRuleError(status_code=409, detail="登录账号已存在")
        user = User(
            user_id=_id("USR"),
            username=username,
            password_hash=hash_password(str(data["password"])),
            display_name=display_name,
            role=role,
            role_definition_id=role_definition.role_id,
            department_id=department_id,
            business_scope=role_definition.business_scope,
            is_active=True,
            is_system_admin=is_system_admin,
            must_change_password=False,
        )
        self.db.add(user)
        self.db.flush()
        self._sync_user_overrides_from_payload(user.user_id, data)
        record_audit_event(
            self.db,
            actor=actor,
            action="admin.user.create",
            target_type="user",
            target_id=user.user_id,
            summary=f"创建账号：{display_name}",
            details={"role": role, "departmentId": user.department_id},
        )
        self._finish(commit)
        return self._user_view(user)

    def update_user(
        self,
        actor: User,
        user_id: str,
        changes: dict[str, Any],
        *,
        commit: bool = True,
    ) -> dict[str, Any]:
        user = self.repository.user(user_id)
        if user is None:
            raise BusinessRuleError(status_code=404, detail="账号不存在")
        if user.deleted_at is not None:
            raise BusinessRuleError(status_code=409, detail="已删除账号不能继续编辑")
        before = self._user_view(user)
        username = changes.get("username")
        if username is not None:
            username = str(username).strip().lower()
            duplicate = self.db.scalar(
                select(User).where(
                    func.lower(User.username) == username.lower(),
                    User.user_id != user_id,
                )
            )
            if duplicate:
                raise BusinessRuleError(status_code=409, detail="登录账号已存在")
            user.username = username
        if changes.get("displayName") is not None:
            user.display_name = str(changes["displayName"]).strip()

        role_definition = self._resolve_role(
            changes.get("roleId") or user.role_definition_id or user.role
        )
        role = role_definition.role_id
        department_id = (
            changes["departmentId"]
            if "departmentId" in changes
            else user.department_id
        )
        self._validate_user_assignment(role, department_id)
        business_scope = role_definition.business_scope
        if business_scope not in {"department", "organization"}:
            raise BusinessRuleError(status_code=422, detail="业务数据范围无效")
        next_admin = (
            bool(changes["isSystemAdmin"])
            if changes.get("isSystemAdmin") is not None
            else user.is_system_admin
        )
        next_active = (
            bool(changes["isActive"])
            if changes.get("isActive") is not None
            else user.is_active
        )
        self._validate_system_admin_assignment(role_definition, next_admin)
        if user.user_id == actor.user_id and (not next_admin or not next_active):
            raise BusinessRuleError(status_code=409, detail="不能移除自己的管理员权限或停用自己的账号")
        if user.is_system_admin and (not next_admin or not next_active):
            remaining = self.db.scalar(
                select(func.count())
                .select_from(User)
                .where(
                    User.is_system_admin.is_(True),
                    User.is_active.is_(True),
                    User.user_id != user.user_id,
                )
            ) or 0
            if remaining == 0:
                raise BusinessRuleError(status_code=409, detail="系统必须至少保留一个启用的管理员")
        if (
            not next_active
            or role != user.role
            or department_id != user.department_id
        ):
            assigned_open_job = self.db.scalar(
                select(Job.job_id).where(
                    or_(
                        Job.hiring_manager_id == user.user_id,
                        Job.department_recruiter_id == user.user_id,
                    ),
                    Job.status == "open",
                    Job.deleted_at.is_(None),
                )
            )
            if assigned_open_job:
                raise BusinessRuleError(
                    status_code=409,
                    detail="该账号仍负责开放岗位，请先完成岗位人员转交",
                )

        user.role = role
        user.role_definition_id = role_definition.role_id
        user.department_id = department_id
        user.business_scope = business_scope
        user.is_system_admin = next_admin
        user.is_active = next_active
        if "responsibilityOverrides" in changes or "permissionOverrides" in changes:
            self._sync_user_overrides_from_payload(user.user_id, changes)
        record_audit_event(
            self.db,
            actor=actor,
            action="admin.user.update",
            target_type="user",
            target_id=user.user_id,
            summary=f"修改账号：{user.display_name}",
            details={"before": before, "after": self._user_view(user)},
        )
        self._finish(commit)
        return self._user_view(user)

    def reset_password(
        self,
        actor: User,
        user_id: str,
        password: str,
        *,
        commit: bool = True,
    ) -> dict[str, Any]:
        user = self.repository.user(user_id)
        if user is None:
            raise BusinessRuleError(status_code=404, detail="账号不存在")
        if user.deleted_at is not None:
            raise BusinessRuleError(status_code=409, detail="已删除账号不能重置密码")
        user.password_hash = hash_password(password)
        user.must_change_password = False
        record_audit_event(
            self.db,
            actor=actor,
            action="admin.user.reset_password",
            target_type="user",
            target_id=user.user_id,
            summary=f"重置账号密码：{user.display_name}",
        )
        self._finish(commit)
        return {"userId": user.user_id, "mustChangePassword": False}

    def soft_delete_user(self, actor: User, user_id: str, *, commit: bool = True) -> dict[str, Any]:
        """逻辑删除账号：禁止后续登录和业务分配，保留全部历史关联和审计记录。"""
        user = self.repository.user(user_id)
        if user is None:
            raise BusinessRuleError(status_code=404, detail="账号不存在")
        if user.deleted_at is not None:
            raise BusinessRuleError(status_code=409, detail="账号已被删除")
        if user.user_id == actor.user_id:
            raise BusinessRuleError(status_code=409, detail="不能删除当前登录账号")
        if user.is_system_admin:
            remaining = self.db.scalar(
                select(func.count()).select_from(User).where(
                    User.is_system_admin.is_(True),
                    User.is_active.is_(True),
                    User.deleted_at.is_(None),
                    User.user_id != user.user_id,
                )
            ) or 0
            if remaining == 0:
                raise BusinessRuleError(status_code=409, detail="系统必须至少保留一个启用的管理员")
        assigned_open_job = self.db.scalar(
            select(Job.job_id).where(
                or_(Job.hiring_manager_id == user.user_id, Job.department_recruiter_id == user.user_id),
                Job.status == "open", Job.deleted_at.is_(None),
            )
        )
        if assigned_open_job:
            raise BusinessRuleError(status_code=409, detail="该账号仍负责开放岗位，请先完成岗位人员转交")
        now = datetime.now(UTC).replace(tzinfo=None)
        user.is_active = False
        user.deleted_at = now
        user.deleted_by_user_id = actor.user_id
        record_audit_event(
            self.db, actor=actor, action="admin.user.delete", target_type="user",
            target_id=user.user_id, summary=f"逻辑删除账号：{user.display_name}",
        )
        self._finish(commit)
        return {"userId": user.user_id, "deleted": True, "deletedAt": utc_iso(now)}

    def _finish(self, commit: bool) -> None:
        # 保留参数仅兼容旧内部调用；提交权只属于外层 CommandRunner。
        self.db.flush()

    def _validate_user_assignment(self, role: str, department_id: str | None) -> None:
        if not department_id:
            raise BusinessRuleError(status_code=400, detail="所有业务账号都必须选择所属部门")
        department = self.repository.department(department_id)
        if department is None or department.deleted_at is not None:
            raise BusinessRuleError(status_code=409, detail="所属部门不存在或未启用")

    def _validate_system_admin_assignment(
        self,
        role: RoleDefinition,
        is_system_admin: bool,
    ) -> None:
        if is_system_admin and role.business_scope != "organization":
            raise BusinessRuleError(
                status_code=422,
                detail="系统管理员必须使用组织范围角色",
            )

    @staticmethod
    def _validate_role_scope_change(
        business_scope: str,
        assigned_users: list[User],
    ) -> None:
        if business_scope not in {"department", "organization"}:
            raise BusinessRuleError(status_code=422, detail="业务数据范围无效")
        if business_scope == "department":
            system_admins = [user.display_name for user in assigned_users if user.is_system_admin]
            if system_admins:
                raise BusinessRuleError(
                    status_code=409,
                    detail="该角色仍绑定系统管理员账号，不能改为本部门范围",
                )

    def _resolve_role(self, role_id: Any) -> RoleDefinition:
        role = self.repository.role(str(role_id or ""))
        if role is None or not role.is_active:
            raise BusinessRuleError(status_code=409, detail="所选角色不存在或已停用")
        return role

    def _validate_role_permissions(
        self, permissions: Any, *, business_scope: str
    ) -> list[str]:
        values = [str(value) for value in permissions]
        unknown = set(values) - set(PERMISSION_LABELS)
        if unknown:
            raise BusinessRuleError(status_code=422, detail=f"未知权限：{', '.join(sorted(unknown))}")
        self._validate_permission_scope(business_scope, values)
        return sorted(set(values))

    def _validate_responsibility_bundles(
        self, bundle_codes: Any, *, business_scope: str
    ) -> list[str]:
        """校验并规范化管理端职责包，旧名称只作为兼容别名接受。

        部门范围可以使用混合职责包（例如岗位编辑与岗位文档导入的组合），
        但不能选择完全属于组织范围的职责包。个人例外是字典形式：部门账号
        可以明确禁止组织职责，却不能用 ``allow`` 绕过原子权限的最低范围。
        """
        values = (
            [str(value) for value in bundle_codes]
            if not isinstance(bundle_codes, dict)
            else [str(value) for value in bundle_codes]
        )
        try:
            normalized = canonical_responsibility_bundle_codes(values)
        except KeyError as exc:
            raise BusinessRuleError(
                status_code=422,
                detail=f"未知职责包：{str(exc.args[0])}",
            ) from exc
        if isinstance(bundle_codes, dict):
            for raw_code, effect in bundle_codes.items():
                if str(effect) not in {"allow", "deny"}:
                    raise BusinessRuleError(status_code=422, detail="职责例外必须是 allow 或 deny")
                canonical = canonical_responsibility_bundle_code(str(raw_code))
                if (
                    business_scope == "department"
                    and canonical is not None
                    and RESPONSIBILITY_BUNDLES[canonical].required_business_scope == "organization"
                    and str(effect) == "allow"
                ):
                    raise BusinessRuleError(
                        status_code=422,
                        detail="部门范围账号不能允许仅全公司范围职责",
                    )
        elif business_scope == "department":
            organization_only = [
                code
                for code in normalized
                if RESPONSIBILITY_BUNDLES[code].required_business_scope == "organization"
            ]
            if organization_only:
                labels = ", ".join(RESPONSIBILITY_BUNDLES[code].label for code in organization_only)
                raise BusinessRuleError(
                    status_code=422,
                    detail=f"仅全公司范围角色可配置职责：{labels}",
                )
        return sorted(normalized)

    @staticmethod
    def _organization_only_permission_codes() -> set[str]:
        return set(PERMISSION_REQUIRED_BUSINESS_SCOPE)

    def _validate_permission_scope(
        self, business_scope: str, permissions: list[str] | set[str]
    ) -> None:
        if business_scope == "organization":
            return
        organization_only = set(permissions) & self._organization_only_permission_codes()
        if organization_only:
            labels = ", ".join(
                PERMISSION_LABELS[code] for code in sorted(organization_only)
            )
            raise BusinessRuleError(
                status_code=422,
                detail=f"仅全公司范围角色可授予权限：{labels}",
            )

    def _role_permissions_from_payload(
        self, data: dict[str, Any], *, business_scope: str
    ) -> list[str]:
        """将新职责包合同转换为角色表继续保存的原子权限码。

        permissions 是旧客户端的兼容字段，不能与职责包并用。新管理端始终提交
        responsibilityBundles，因此角色默认职责不会因前端分组变化而漂移。
        """
        bundles = data.get("responsibilityBundles")
        if bundles is not None:
            if data.get("permissions") is not None:
                raise BusinessRuleError(
                    status_code=422,
                    detail="角色权限请仅提交职责包，不能同时提交原子权限",
                )
            return sorted(role_permissions_for_bundles(
                self._validate_responsibility_bundles(
                    bundles, business_scope=business_scope,
                ),
                business_scope=business_scope,
            ))
        return self._validate_role_permissions(
            data.get("permissions") or [], business_scope=business_scope,
        )

    def _role_view(self, role: RoleDefinition) -> dict[str, Any]:
        user_count = self.db.scalar(
            select(func.count()).select_from(User).where(User.role_definition_id == role.role_id)
        ) or 0
        return {
            "roleId": role.role_id,
            "name": role.name,
            "businessScope": role.business_scope,
            "permissions": sorted(
                code
                for code, enabled in (role.permissions or {}).items()
                if enabled and code in PERMISSION_LABELS
            ),
            # 旧角色可能仍保留细粒度权限；这里只投影已完整覆盖的职责包，
            # 管理员保存后会统一为当前的职责包组合。
            "responsibilityBundles": responsibility_bundle_codes_for_permissions(
                (
                    code
                    for code, enabled in (role.permissions or {}).items()
                    if enabled and code in PERMISSION_LABELS
                ),
                business_scope=role.business_scope,
            ),
            "responsibilityCatalog": self._responsibility_catalog(),
            "isSystem": role.is_system,
            "isActive": role.is_active,
            "userCount": user_count,
        }

    def _sync_permission_overrides(
        self,
        user_id: str,
        changes: dict[str, str],
    ) -> None:
        unknown = set(changes) - set(PERMISSION_LABELS)
        if unknown:
            raise BusinessRuleError(
                status_code=422,
                detail=f"未知权限：{', '.join(sorted(unknown))}",
            )
        existing = {
            row.permission_code: row
            for row in self.db.scalars(
                select(UserPermissionOverride).where(
                    UserPermissionOverride.user_id == user_id
                )
            )
        }
        for code in PERMISSION_LABELS:
            if code not in changes:
                row = existing.get(code)
                if row is not None:
                    self.db.delete(row)
                continue
            effect = str(changes[code])
            if effect not in {"allow", "deny"}:
                raise BusinessRuleError(status_code=422, detail=f"权限设置无效：{code}")
            row = existing.get(code)
            if row is None:
                row = UserPermissionOverride(
                    permission_override_id=_id("UPO"),
                    user_id=user_id,
                    permission_code=code,
                    effect=effect,
                    created_at=datetime.now(UTC).replace(tzinfo=None),
                    updated_at=datetime.now(UTC).replace(tzinfo=None),
                )
                self.db.add(row)
            else:
                row.effect = effect
                row.updated_at = datetime.now(UTC).replace(tzinfo=None)

    def _sync_user_overrides_from_payload(
        self,
        user_id: str,
        data: dict[str, Any],
    ) -> None:
        """保存个人职责例外，并在写库边界展开为原子权限覆盖。

        展开后的 allow/deny 记录继续由 PermissionResolutionService 计算，
        因此现有接口无需了解管理页面的职责包概念。职责包请求会清除历史的
        细粒度覆盖，避免同一账号同时存在两种互相矛盾的配置来源。
        """
        bundles = data.get("responsibilityOverrides")
        if bundles is not None:
            if data.get("permissionOverrides"):
                raise BusinessRuleError(
                    status_code=422,
                    detail="个人权限请仅提交职责包例外，不能同时提交原子权限",
                )
            user = self.repository.user(user_id)
            if user is None:
                raise BusinessRuleError(status_code=404, detail="账号不存在")
            self._validate_responsibility_bundles(
                bundles, business_scope=user.business_scope,
            )
            canonical_overrides: dict[str, str] = {}
            for raw_code, effect in bundles.items():
                canonical = canonical_responsibility_bundle_code(str(raw_code))
                if canonical is not None:
                    canonical_overrides[canonical] = str(effect)
            role_definition = (
                self.repository.role(user.role_definition_id)
                if user.role_definition_id
                else None
            )
            role_defaults = (
                {
                    code
                    for code, enabled in (role_definition.permissions or {}).items()
                    if enabled and code in PERMISSION_LABELS
                }
                if role_definition and role_definition.is_active
                else ROLE_DEFAULT_PERMISSIONS.get(user.role, set())
            )
            # 前端提交的是每项职责当前的最终状态，而不是让管理员再理解“继承”。
            # 在写库边界只记录与角色默认值不同的原子权限，角色调整后仍能
            # 正确继承；同一职责包内的权限始终作为一个不可拆分的单元处理。
            expanded = {
                permission_code: effect
                for bundle_code, effect in canonical_overrides.items()
                for permission_code in RESPONSIBILITY_BUNDLES[bundle_code].permission_codes
                if not (
                    user.business_scope == "department"
                    and permission_code in PERMISSION_REQUIRED_BUSINESS_SCOPE
                )
            }
            overrides = {
                permission_code: effect
                for permission_code, effect in expanded.items()
                if (permission_code in role_defaults) != (effect == "allow")
            }
            self._sync_permission_overrides(user_id, overrides)
            return
        raw_overrides = dict(data.get("permissionOverrides") or {})
        user = self.repository.user(user_id)
        if user is None:
            raise BusinessRuleError(status_code=404, detail="账号不存在")
        self._validate_permission_scope(
            user.business_scope,
            [code for code, effect in raw_overrides.items() if effect == "allow"],
        )
        self._sync_permission_overrides(user_id, raw_overrides)

    def _business_permission_overrides(self, user_id: str) -> dict[str, str]:
        return {
            code: effect
            for code, effect in permission_overrides(self.db, user_id).items()
            if code in PERMISSION_LABELS
        }

    def _responsibility_overrides(
        self, user_id: str, *, business_scope: str | None = None
    ) -> dict[str, str]:
        """把完整的历史原子覆盖投影为管理端可编辑的职责包例外。

        不完整的旧覆盖继续保留在数据库中以免改变既有账号能力，但不会在新页面
        中伪装成完整职责包；用户主动保存例外时会由新合同统一替换。
        """
        overrides = self._business_permission_overrides(user_id)
        result: dict[str, str] = {}
        for bundle_code, bundle in RESPONSIBILITY_BUNDLES.items():
            permission_codes = set(bundle.permission_codes)
            if business_scope == "department":
                permission_codes.difference_update(PERMISSION_REQUIRED_BUSINESS_SCOPE)
            effects = {overrides.get(code) for code in permission_codes}
            if len(effects) == 1 and None not in effects:
                result[bundle_code] = effects.pop() or "deny"
        return result

    @staticmethod
    def _responsibility_catalog() -> list[dict[str, Any]]:
        """提供给管理端的职责包说明；原子权限码不作为日常配置界面合同。"""
        return [
            {
                "code": bundle.code,
                "label": bundle.label,
                "description": bundle.description,
                **(
                    {"requiredBusinessScope": bundle.required_business_scope}
                    if bundle.required_business_scope is not None
                    else {}
                ),
                **(
                    {
                        "organizationOnlyOperations": [
                            PERMISSION_LABELS[permission_code]
                            for permission_code in sorted(bundle.permission_codes)
                            if permission_code in PERMISSION_REQUIRED_BUSINESS_SCOPE
                        ]
                    }
                    if any(
                        permission_code in PERMISSION_REQUIRED_BUSINESS_SCOPE
                        for permission_code in bundle.permission_codes
                    )
                    and bundle.required_business_scope is None
                    else {}
                ),
            }
            for bundle in RESPONSIBILITY_BUNDLES.values()
        ]

    def _user_view(self, user: User) -> dict[str, Any]:
        role_definition = (
            self.repository.role(user.role_definition_id)
            if user.role_definition_id
            else None
        )
        role_defaults = (
            {
                code
                for code, enabled in (role_definition.permissions or {}).items()
                if enabled and code in PERMISSION_LABELS
            }
            if role_definition
            else ROLE_DEFAULT_PERMISSIONS.get(user.role, set())
        )
        effective = effective_permissions(self.db, user)
        return {
            "userId": user.user_id,
            "username": user.username or "",
            "displayName": user.display_name,
            "role": user.role,
            "roleId": role_definition.role_id if role_definition else user.role,
            "roleName": role_definition.name if role_definition else user.role,
            "departmentId": user.department_id,
            "businessScope": user.business_scope,
            "permissionOverrides": self._business_permission_overrides(user.user_id),
            "responsibilityOverrides": self._responsibility_overrides(
                user.user_id, business_scope=user.business_scope
            ),
            "roleDefaultPermissions": sorted(
                role_defaults
            ),
            "roleResponsibilityBundles": responsibility_bundle_codes_for_permissions(
                role_defaults,
                business_scope=user.business_scope,
            ),
            "effectivePermissions": sorted(effective),
            "effectiveResponsibilityBundles": responsibility_bundle_codes_for_permissions(
                effective,
                business_scope=user.business_scope,
            ),
            "permissionCatalog": PERMISSION_LABELS,
            "responsibilityCatalog": self._responsibility_catalog(),
            "isSystemAdmin": user.is_system_admin,
            "isActive": user.is_active,
            "deletedAt": _iso(user.deleted_at),
            "mustChangePassword": user.must_change_password,
            "lastLoginAt": _iso(user.last_login_at),
        }

