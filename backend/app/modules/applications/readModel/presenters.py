from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from backend.app.models.entities import Application, Department, Job, User


def _iso(value: datetime | None) -> str:
    return value.isoformat(sep=" ", timespec="seconds") if value else ""


def department_name(db: Session, job: Job | None) -> str:
    """返回面向用户的部门名称，绝不把数据库部门 ID 暴露给页面。"""
    if job is None:
        return ""
    department = db.get(Department, job.department_id)
    if department is not None and department.name:
        return department.name
    return "未设置"


def application_view(db: Session, app: Application) -> dict[str, Any]:
    job = db.get(Job, app.job_id)
    # 岗位尚未配置部门招聘人时，申请可停在初筛审核前；读模型应安全展示为空。
    first_user = db.get(User, app.assigned_first_interviewer) if app.assigned_first_interviewer else None
    hr_user = db.get(User, app.assigned_hr) if app.assigned_hr else None
    return {
        "applicationId": app.application_id,
        "candidateId": app.candidate_id,
        "jobId": app.job_id,
        "status": app.status,
        "department": department_name(db, job),
        "currentOwner": app.current_owner,
        "assignedFirstInterviewer": first_user.display_name if first_user else "",
        "assignedSecondHr": hr_user.display_name if hr_user else "",
        "submittedAt": _iso(app.submitted_at),
        "updatedAt": _iso(app.updated_at),
        "dueAt": _iso(app.due_at),
        "overdue": False,
        "resumeSubmissionId": app.adopted_resume_submission_id,
    }


def job_view(job: Job, db: Session) -> dict[str, Any]:
    headcount = int(job.headcount or 0)
    return {
        "jobId": job.job_id,
        "title": job.title,
        "department": department_name(db, job),
        "headcount": headcount,
        "openHeadcount": headcount,
        "jdTextPreview": job.jd_text[:500],
        "responsibilities": list(job.responsibilities or []),
        "qualifications": list(job.qualifications or []),
        "majorRequirement": job.major_requirement or "",
        "educationRequirement": job.education_requirement or "",
        "sourceDocumentId": job.source_document_id,
    }
