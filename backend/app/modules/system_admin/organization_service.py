from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from backend.app.shared.errors import BusinessRuleError
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from backend.app.models.entities import Application, Department, Job, User
from backend.app.modules.applications.public import blocks_owner_deletion
from backend.app.modules.jobs.public import JobProcessService
from backend.app.modules.jobs.public import JobLifecycleService
from backend.app.modules.tasks.public import TaskWriteService
from backend.app.shared.audit import record_audit_event
from backend.app.shared.time_serialization import utc_iso
from backend.app.modules.auth.public import has_permission
from backend.app.modules.system_admin.repository import SystemAdminRepository


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20].upper()}"


def _iso(value: datetime | None) -> str | None:
    return utc_iso(value) if value else None


def _is_department_manager(user: User) -> bool:
    """岗位主管资格由部门主管角色确定，不由可配置业务权限授予。"""
    return (
        user.role_definition_id == "department_manager"
        or user.role == "department_manager"
    )


class OrganizationAdminService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repository = SystemAdminRepository(db)

    def list_departments(self) -> list[dict[str, Any]]:
        rows = self.db.scalars(
            select(Department).order_by(Department.deleted_at.is_not(None), Department.name)
        ).all()
        users = self.db.scalars(select(User)).all()
        names = {user.user_id: user.display_name for user in users}
        results: list[dict[str, Any]] = []
        for department in rows:
            department_users = [
                user for user in users
                if user.department_id == department.department_id and user.is_active
            ]
            manager_count = sum(_is_department_manager(user) for user in department_users)
            recruiter_count = sum(
                has_permission(self.db, user, "first_interview.manage")
                for user in department_users
            )
            open_job_count = int(self.db.scalar(
                select(func.count()).select_from(Job).where(
                    Job.department_id == department.department_id,
                    Job.status == "open",
                    Job.deleted_at.is_(None),
                )
            ) or 0)
            active_application_count = int(self.db.scalar(
                select(func.count()).select_from(Application).where(
                    Application.department_id == department.department_id,
                    Application.deleted_at.is_(None),
                    Application.status.in_(tuple(_blocking_statuses())),
                )
            ) or 0)
            results.append({
                "departmentId": department.department_id,
                "name": department.name,
                "managerCount": manager_count,
                "recruiterCount": recruiter_count,
                "activeUserCount": len(department_users),
                "openJobCount": open_job_count,
                "activeApplicationCount": active_application_count,
                "deletedAt": _iso(department.deleted_at),
                "deletedByName": names.get(department.deleted_by_user_id or "", ""),
                "lifecycleStatus": "deleted" if department.deleted_at else "normal",
            })
        return results

    def create_department(self, actor: User, name: str, *, commit: bool = True) -> dict[str, Any]:
        clean_name = name.strip()
        if self.db.scalar(
            select(Department).where(func.lower(Department.name) == clean_name.lower())
        ):
            raise BusinessRuleError(status_code=409, detail="部门名称已存在")
        department = Department(department_id=_id("DEPT"), name=clean_name, is_active=True)
        self.db.add(department)
        record_audit_event(

            self.db, actor=actor, action="admin.department.create", target_type="department",
            target_id=department.department_id, summary=f"创建部门：{clean_name}",
        )
        self._finish(commit)
        return self._department_view(department, actor_name="")

    def update_department(self, actor: User, department_id: str, changes: dict[str, Any], *, commit: bool = True) -> dict[str, Any]:
        department = self.repository.department(department_id)
        if department is None or department.deleted_at is not None:
            raise BusinessRuleError(status_code=404, detail="部门不存在")
        if changes.get("name") is None:
            return self._department_view(department, actor_name="")
        name = str(changes["name"]).strip()
        duplicate = self.db.scalar(select(Department).where(
            func.lower(Department.name) == name.lower(), Department.department_id != department_id,
        ))
        if duplicate:
            raise BusinessRuleError(status_code=409, detail="部门名称已存在")
        before_name = department.name
        department.name = name
        record_audit_event(

            self.db, actor=actor, action="admin.department.update", target_type="department",
            target_id=department.department_id, summary=f"修改部门：{department.name}",
            details={"before": {"name": before_name}, "after": {"name": department.name}},
        )
        self._finish(commit)
        return self._department_view(department, actor_name="")

    def soft_delete_department(self, actor: User, department_id: str, *, commit: bool = True) -> dict[str, Any]:
        department = self.db.scalar(
            select(Department).where(Department.department_id == department_id).with_for_update()
        )
        if department is None or department.deleted_at is not None:
            raise BusinessRuleError(status_code=404, detail="部门不存在")
        active_user_count = int(self.db.scalar(
            select(func.count()).select_from(User).where(
                User.department_id == department_id,
                User.is_active.is_(True),
                User.deleted_at.is_(None),
            )
        ) or 0)
        if active_user_count:
            raise BusinessRuleError(
                status_code=409,
                detail=f"该部门仍有 {active_user_count} 个启用账号，请先停用、删除或转移账号",
            )
        jobs = self.db.scalars(
            select(Job).where(Job.department_id == department_id, Job.deleted_at.is_(None)).with_for_update()
        ).all()
        now = datetime.now(UTC).replace(tzinfo=None)
        JobLifecycleService(self.db).soft_delete_jobs(
            actor, jobs, now=now, reason="department_deleted"
        )
        department.is_active = False
        department.deleted_at = now
        department.deleted_by_user_id = actor.user_id
        record_audit_event(

            self.db, actor=actor, action="admin.department.delete", target_type="department",
            target_id=department.department_id, summary=f"删除部门：{department.name}",
            details={"deleteMode": "soft", "jobCount": len(jobs)},
        )
        self._finish(commit)
        return {
            "departmentId": department.department_id,
            "deleted": True,
            "deletedAt": utc_iso(now),
            "deletedJobCount": len(jobs),
        }

    def list_jobs(self) -> list[dict[str, Any]]:
        departments = {row.department_id: row.name for row in self.db.scalars(select(Department))}
        users = {row.user_id: row.display_name for row in self.db.scalars(select(User))}
        # 岗位人员配置只面向当前有效岗位；软删除岗位仍保留在数据库中供
        # 申请和审计历史关联，但不应继续出现在配置列表。
        jobs = self.db.scalars(
            select(Job).where(Job.deleted_at.is_(None)).order_by(
                Job.position_id,
                case((Job.status == "open", 0), (Job.status == "setup_pending", 1), else_=2),
                Job.opened_at.desc().nullslast(),
                Job.job_id.desc(),
            )
        ).all()
        # 一个 Position 可保留多个历史招聘批次；管理页只配置当前批次，避免把已关闭
        # 的旧批次再次显示成一个可编辑岗位。开放、待配置优先，其余取最近批次。
        current_jobs: dict[str, Job] = {}
        for job in jobs:
            current_jobs.setdefault(job.position_id or job.job_id, job)
        return [
            self._job_view(job, departments, users)
            for job in sorted(
                current_jobs.values(),
                key=lambda item: (item.status != "open", item.department_id, item.title),
            )
        ]

    def update_job(self, actor: User, job_id: str, changes: dict[str, Any], *, commit: bool = True) -> dict[str, Any]:
        job = self.repository.job(job_id)
        if job is None or job.deleted_at is not None:
            raise BusinessRuleError(status_code=404, detail="岗位不存在")
        before = {
            "status": job.status, "headcount": job.headcount,
            "hiringManagerId": job.hiring_manager_id,
            "departmentRecruiterId": job.department_recruiter_id,
        }
        if changes.get("headcount") is not None:
            job.headcount = int(changes["headcount"])
        if "hiringManagerId" in changes:
            manager_id = changes["hiringManagerId"]
            if manager_id:
                manager = self.repository.user(manager_id)
                if (manager is None or not manager.is_active or
                    not _is_department_manager(manager) or
                    manager.department_id != job.department_id):
                    raise BusinessRuleError(status_code=409, detail="岗位部门主管必须是本部门启用的部门主管账号")
            job.hiring_manager_id = manager_id or None
        if "departmentRecruiterId" in changes:
            recruiter_id = changes["departmentRecruiterId"]
            if recruiter_id:
                recruiter = self.repository.user(recruiter_id)
                if (recruiter is None or not recruiter.is_active or
                    not has_permission(self.db, recruiter, "first_interview.manage") or
                    recruiter.department_id != job.department_id):
                    raise BusinessRuleError(status_code=409, detail="岗位部门招聘人必须是本部门启用的招聘人")
            job.department_recruiter_id = recruiter_id or None
        now = datetime.now(UTC).replace(tzinfo=None)
        if changes.get("status") is not None:
            requested_status = str(changes["status"])
            if requested_status != job.status:
                actions = {
                    ("draft", "open"): JobProcessService.publish_job,
                    ("setup_pending", "open"): JobProcessService.publish_job,
                    ("open", "setup_pending"): JobProcessService.require_setup,
                    ("open", "closed"): JobProcessService.close_job,
                    ("closed", "open"): JobProcessService.reopen_job,
                }
                transition = actions.get((str(job.status), requested_status))
                if transition is None:
                    raise BusinessRuleError(status_code=409, detail=f"岗位不允许从 {job.status} 变为 {requested_status}")
                if requested_status == "open" and (not job.hiring_manager_id or not job.department_recruiter_id):
                    raise BusinessRuleError(status_code=409, detail="开放岗位必须同时配置部门主管和部门招聘人")
                transition(job, now=now)

        # 管理员清空主管或招聘人时，自动回到待完成配置；这不是失败，
        # 也不能让一个缺少必要配置的岗位继续保持 open。
        if job.status == "open" and (not job.hiring_manager_id or not job.department_recruiter_id):
            JobProcessService.require_setup(job, now=now)

        if job.status == "open":
            department = self.repository.department(job.department_id)
            if department is None or department.deleted_at is not None:
                raise BusinessRuleError(status_code=409, detail="岗位所属部门已删除")
            other_open = self.db.scalar(select(Job.job_id).where(
                Job.position_id == job.position_id, Job.status == "open",
                Job.deleted_at.is_(None), Job.job_id != job.job_id,
            ))
            if other_open:
                raise BusinessRuleError(status_code=409, detail="该岗位已有开放中的招聘批次")
            job.opened_at = job.opened_at or now
            job.closed_at = None
        elif job.status == "setup_pending":
            job.closed_at = None
        elif before["status"] == "open":
            job.closed_at = now
        # 对历史申请同步刚完成的岗位人员配置。初筛已完成但当时无部门招聘人的
        # 申请此前没有待办；配置完成后在同一命令事务内补齐，避免业务结果与待办脱节。
        self._sync_applications_after_recruiter_assignment(job)
        record_audit_event(

            self.db, actor=actor, action="admin.job.update", target_type="job", target_id=job.job_id,
            summary=f"修改岗位：{job.title}",
            details={"before": before, "after": {"status": job.status, "headcount": job.headcount, "hiringManagerId": job.hiring_manager_id, "departmentRecruiterId": job.department_recruiter_id}},
        )
        self._finish(commit)
        return self._job_view(job, {job.department_id: department.name if job.status == "open" else (self.repository.department(job.department_id).name if self.repository.department(job.department_id) else "")}, {row.user_id: row.display_name for row in self.db.scalars(select(User))})

    def _finish(self, commit: bool) -> None:
        # 保留参数仅兼容旧内部调用；提交权只属于外层 CommandRunner。
        self.db.flush()

    def _sync_applications_after_recruiter_assignment(self, job: Job) -> None:
        """将岗位招聘人投影到未指派申请，并补建已完成初筛的部门审核待办。

        此方法只准备 ORM 变更，不自行提交；外层 ``CommandRunner`` 保证岗位配置、
        申请负责人和待办创建在同一原子事务中生效。
        """
        recruiter_id = job.department_recruiter_id
        if not recruiter_id:
            return
        applications = self.db.scalars(
            select(Application).where(
                Application.job_id == job.job_id,
                Application.deleted_at.is_(None),
            )
        ).all()
        tasks = TaskWriteService(self.db)
        for application in applications:
            if not application.assigned_first_interviewer:
                application.assigned_first_interviewer = recruiter_id
            if application.status == "department_review":
                tasks.ensure_pending(
                    application_id=application.application_id,
                    task_type="department_review",
                    title="部门初步筛选审核",
                    assignee_user_id=application.assigned_first_interviewer,
                    assignee_role="department_recruiter",
                )
    def _department_view(self, department: Department, *, actor_name: str) -> dict[str, Any]:
        users = self.db.scalars(select(User).where(User.department_id == department.department_id, User.is_active.is_(True))).all()
        return {
            "departmentId": department.department_id,
            "name": department.name,
            "managerCount": sum(_is_department_manager(user) for user in users),
            "recruiterCount": sum(has_permission(self.db, user, "first_interview.manage") for user in users),
            "activeUserCount": len(users),
            "openJobCount": int(self.db.scalar(select(func.count()).select_from(Job).where(Job.department_id == department.department_id, Job.status == "open", Job.deleted_at.is_(None))) or 0),
            "activeApplicationCount": int(self.db.scalar(select(func.count()).select_from(Application).where(Application.department_id == department.department_id, Application.deleted_at.is_(None), Application.status.in_(tuple(_blocking_statuses())))) or 0),
            "deletedAt": _iso(department.deleted_at),
            "deletedByName": actor_name,
            "lifecycleStatus": "deleted" if department.deleted_at else "normal",
        }

    def _job_view(self, job: Job, departments: dict[str, str], users: dict[str, str]) -> dict[str, Any]:
        return {
            "jobId": job.job_id,
            "positionId": job.position_id,
            "title": job.title,
            "departmentId": job.department_id,
            "departmentName": departments.get(job.department_id, ""),
            "status": job.status,
            "headcount": job.headcount,
            "hiringManagerId": job.hiring_manager_id,
            "hiringManagerName": users.get(job.hiring_manager_id or "", ""),
            "departmentRecruiterId": job.department_recruiter_id,
            "departmentRecruiterName": users.get(job.department_recruiter_id or "", ""),
            "applicationCount": int(self.db.scalar(select(func.count()).select_from(Application).where(Application.job_id == job.job_id, Application.deleted_at.is_(None))) or 0),
            "activeApplicationCount": int(self.db.scalar(select(func.count()).select_from(Application).where(Application.job_id == job.job_id, Application.deleted_at.is_(None), Application.status.in_(tuple(_blocking_statuses())))) or 0),
            "deletedAt": _iso(job.deleted_at),
            "deletedByName": users.get(job.deleted_by_user_id or "", ""),
            "lifecycleStatus": "deleted" if job.deleted_at else job.status,
        }


def _blocking_statuses() -> set[str]:
    return {
        "first_interview_planning", "first_interview_scheduled", "first_interview_in_progress",
        "first_interview_evaluation", "hr_second_review", "second_interview_in_progress",
        "second_interview_evaluation", "final_review",
    }
