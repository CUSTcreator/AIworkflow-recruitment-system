"""Job 模块向 Candidate/Application 暴露的只读岗位版本与画像就绪契约。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.entities import Department, Job, JobRequirementProfileRecord, JobVersionRecord
from backend.app.modules.jobs.profile_readiness import evaluate_job_profile_record
from backend.app.shared.errors import BusinessError


@dataclass(frozen=True)
class JobRoutingVersion:
    """已确认且未关闭的一版岗位快照。

    is_routable 只表示可以用于专业匹配和创建 Application；
    is_screening_ready 才表示存在可被冻结到 Application 的岗位能力画像。
    两个判断刻意分离，避免把画像处理进度误当作岗位分发资格。
    """

    job_id: str
    jd_version_id: str
    job_profile_id: str | None
    title: str
    department_id: str
    department_name: str
    major_requirement: str | None
    source_sha256: str
    job_status: str
    profile_status: str | None
    is_routable: bool
    is_screening_ready: bool


@dataclass(frozen=True)
class RoutableJobVersion:
    """已具备有效岗位画像、可立即进入初筛的一版冻结岗位输入。"""

    job_id: str
    jd_version_id: str
    job_profile_id: str
    title: str
    department_id: str
    department_name: str
    major_requirement: str | None
    source_sha256: str


@dataclass(frozen=True)
class JobProfileReadyEvent:
    """岗位画像发布后的稳定事件载荷；订阅端据此唤醒等待中的 Application。"""

    event_id: str
    job_id: str
    jd_version_id: str
    job_profile_id: str
    occurred_at: datetime


def list_open_job_routing_versions(db: Session) -> list[JobRoutingVersion]:
    """返回每个可分发岗位的最新 JD 版本及其画像可用性。

    setup_pending 的岗位已经完成 JD 确认，允许候选人分发；它仅缺少后续面试
    组织配置。真正 closed 的岗位不允许产生新 Application。
    """
    rows = db.execute(
        select(Job, JobVersionRecord, JobRequirementProfileRecord, Department)
        .join(JobVersionRecord, JobVersionRecord.job_id == Job.job_id)
        .outerjoin(Department, Department.department_id == Job.department_id)
        .outerjoin(
            JobRequirementProfileRecord,
            JobRequirementProfileRecord.job_profile_id
            == JobVersionRecord.active_job_profile_id,
        )
        .where(
            Job.status.in_(("setup_pending", "open")),
            Job.deleted_at.is_(None),
        )
        .order_by(Job.job_id, JobVersionRecord.version.desc())
    ).all()
    latest: list[JobRoutingVersion] = []
    seen_job_ids: set[str] = set()
    for job, version, profile, department in rows:
        if job.job_id in seen_job_ids:
            continue
        seen_job_ids.add(job.job_id)
        screening_readiness = evaluate_job_profile_record(
            profile,
            job_id=job.job_id,
            jd_version_id=version.jd_version_id,
        )
        latest.append(
            JobRoutingVersion(
                job_id=job.job_id,
                jd_version_id=version.jd_version_id,
                # 不把历史空画像 ID 带入岗位匹配工件。Application 在创建时只冻结
                # 已通过同一内容校验的画像；无有效画像时进入 waiting_job_profile。
                job_profile_id=(
                    profile.job_profile_id
                    if profile is not None and screening_readiness.ready
                    else None
                ),
                title=job.title,
                department_id=job.department_id,
                department_name=department.name if department is not None else "",
                major_requirement=job.major_requirement,
                source_sha256=version.source_sha256,
                job_status=job.status,
                profile_status=getattr(version, "profile_status", None),
                is_routable=True,
                is_screening_ready=screening_readiness.ready,
            )
        )
    return latest


def list_matchable_job_versions(db: Session) -> list[JobRoutingVersion]:
    """专业匹配的公开入口：岗位已确认且未关闭即可参与。"""
    return list_open_job_routing_versions(db)


def list_routable_job_versions(db: Session) -> list[RoutableJobVersion]:
    """只返回已拥有有效岗位画像、可立即开始初筛的冻结岗位版本。"""
    return [
        RoutableJobVersion(
            job_id=item.job_id,
            jd_version_id=item.jd_version_id,
            job_profile_id=str(item.job_profile_id),
            title=item.title,
            department_id=item.department_id,
            department_name=item.department_name,
            major_requirement=item.major_requirement,
            source_sha256=item.source_sha256,
        )
        for item in list_open_job_routing_versions(db)
        if item.is_screening_ready and item.job_profile_id
    ]


def get_routable_job_version(db: Session, *, job_id: str, jd_version_id: str) -> RoutableJobVersion:
    """按冻结 ID 读取可立即进入初筛的岗位版本。"""
    for item in list_routable_job_versions(db):
        if item.job_id == job_id and item.jd_version_id == jd_version_id:
            return item
    raise BusinessError("job_version_not_screening_ready", "岗位不存在、已关闭或岗位画像未就绪", status_code=409)


def build_job_profile_ready_event(
    *,
    event_id: str,
    job_id: str,
    jd_version_id: str,
    job_profile_id: str,
    occurred_at: datetime,
) -> JobProfileReadyEvent:
    """构造可持久化/可投递的事件；实际发布由岗位画像 Workflow 的短事务负责。"""
    return JobProfileReadyEvent(
        event_id=event_id,
        job_id=job_id,
        jd_version_id=jd_version_id,
        job_profile_id=job_profile_id,
        occurred_at=occurred_at,
    )
