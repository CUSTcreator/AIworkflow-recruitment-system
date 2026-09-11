"""Application 模块的跨模块写入契约。

Candidate 路由只能调用这里创建申请；岗位画像 Workflow 也只能通过这里唤醒
waiting_job_profile 申请。函数均不自行提交事务，由 CommandRunner 或 StepRunner
负责同一短事务内的提交、审计与检查点。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import logging
import uuid

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import Session

from backend.app.infrastructure.lazy_dependency import LazyCallable

from backend.app.models.entities import (
    Application,
    Candidate,
    Job,
    JobRequirementProfileRecord,
    JobVersionRecord,
    ResumeProfileRecord,
    ResumeSubmission,
    SourceDocument,
    StageHistory,
    User,
    WorkflowRun,
    WorkflowStepCheckpoint,
)
from backend.app.modules.applications.application_process_service import ApplicationProcessService
from backend.app.modules.applications.application_recovery import (
    ApplicationRecoveryCode,
    clear_application_recovery,
    set_application_recovery,
)
from backend.app.modules.applications.commands.application_intake_commands import ApplicationIntakeCommands
from backend.app.modules.applications.domain.application_state_machine import WAITING_JOB_PROFILE_STATUS
from backend.app.modules.jobs.public import evaluate_job_profile_record
from backend.app.shared.errors import BusinessError


logger = logging.getLogger(__name__)

# 评估调度在模块加载阶段会依赖 Candidate 重建服务；以模块级显式惰性契约断开启动循环。
enqueue_initial_assessment = LazyCallable(
    "backend.app.modules.assessment.services.assessment_scheduling_service", "enqueue_initial_assessment"
)
enqueue_resume_rebuild_rescoring = LazyCallable(
    "backend.app.modules.assessment.services.assessment_scheduling_service", "enqueue_resume_rebuild_rescoring"
)
enqueue_screening_retry_after_job_profile = LazyCallable(
    "backend.app.modules.assessment.services.assessment_scheduling_service",
    "enqueue_screening_retry_after_job_profile",
)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@dataclass(frozen=True)
class CreateApplicationFromRouting:
    """Candidate 分发发布给 Application 的冻结创建命令。"""

    candidate_id: str
    resume_submission_id: str
    resume_profile_id: str
    source_document_id: str
    job_id: str
    jd_version_id: str
    job_profile_id: str | None
    routing_workflow_run_id: str
    idempotency_key: str


@dataclass(frozen=True)
class ApplicationCreationResult:
    application_id: str
    created: bool
    assessment_enqueued: bool


@dataclass(frozen=True)
class ResumeRebuildRequest:
    """Candidate 简历重建完成后交给 Application 的冻结版本切换命令。"""

    candidate_id: str
    resume_submission_id: str
    resume_profile_id: str
    source_document_id: str
    rebuild_workflow_run_id: str
    idempotency_key: str


@dataclass(frozen=True)
class ResumeRebuildResult:
    application_ids: list[str]
    reassessment_enqueued: bool


def _actor(db: Session, actor_id: str) -> User:
    actor = db.get(User, actor_id)
    if actor is None:
        raise BusinessError("application_actor_not_found", "执行岗位申请操作的用户不存在", status_code=404)
    return actor


def create_application_from_routing(
    db: Session,
    *,
    command: CreateApplicationFromRouting,
    actor_id: str,
) -> ApplicationCreationResult:
    """由岗位分发创建 Application；只有画像就绪时才入队初筛。"""
    actor = _actor(db, actor_id)
    candidate = db.get(Candidate, command.candidate_id)
    submission = db.get(ResumeSubmission, command.resume_submission_id)
    profile = db.get(ResumeProfileRecord, command.resume_profile_id)
    document = db.get(SourceDocument, command.source_document_id)
    job = db.get(Job, command.job_id)
    version = db.get(JobVersionRecord, command.jd_version_id)
    if any(item is None for item in (candidate, submission, profile, document, job, version)):
        raise BusinessError("application_routing_reference_missing", "创建申请的冻结来源不完整", status_code=409)
    if submission.candidate_id != candidate.candidate_id or profile.candidate_id != candidate.candidate_id:
        raise BusinessError("application_routing_candidate_mismatch", "候选人和简历版本不一致", status_code=409)
    if submission.source_document_id != document.source_document_id:
        raise BusinessError("application_routing_document_mismatch", "简历文件版本不一致", status_code=409)
    if version.job_id != job.job_id:
        raise BusinessError("application_routing_job_mismatch", "岗位和冻结 JD 版本不一致", status_code=409)

    frozen_profile_id = command.job_profile_id
    if frozen_profile_id:
        profile_record = db.get(JobRequirementProfileRecord, frozen_profile_id)
        if (
            profile_record is None
            or profile_record.job_id != job.job_id
            or profile_record.jd_version_id != version.jd_version_id
        ):
            raise BusinessError("application_routing_job_profile_mismatch", "岗位能力画像与冻结 JD 版本不一致", status_code=409)

    existing = db.scalar(
        select(Application).where(
            Application.candidate_id == candidate.candidate_id,
            Application.job_id == job.job_id,
            Application.deleted_at.is_(None),
        )
    )
    application = ApplicationIntakeCommands(db).create_from_candidate(
        user=actor,
        candidate=candidate,
        profile=profile,
        job=job,
        job_version=version,
        document=document,
        submitted_at=_now(),
        frozen_job_profile_id=frozen_profile_id,
    )
    # 即使 Candidate 当前版本在并发中发生变化，也必须采用路由时冻结的 Submission。
    application.adopted_resume_submission_id = submission.resume_submission_id
    application.source_resume_profile_id = profile.resume_profile_id
    created = existing is None
    assessment_enqueued = False
    if created and application.job_profile_id:
        enqueue_initial_assessment(
            db,
            user=actor,
            application_ids=[application.application_id],
            source_key=f"routing:{command.idempotency_key}",
        )
        assessment_enqueued = True
    return ApplicationCreationResult(
        application_id=application.application_id,
        created=created,
        assessment_enqueued=assessment_enqueued,
    )


def release_applications_waiting_for_job_profile(
    db: Session,
    *,
    job_id: str,
    jd_version_id: str,
    job_profile_id: str,
    actor_id: str,
    source_key: str,
) -> list[str]:
    """画像发布后逐个唤醒等待申请，单条失败不阻塞同岗位其他申请。"""
    actor = _actor(db, actor_id)
    profile = db.get(JobRequirementProfileRecord, job_profile_id)
    readiness = evaluate_job_profile_record(
        profile,
        job_id=job_id,
        jd_version_id=jd_version_id,
    )
    if not readiness.ready:
        raise BusinessError("job_profile_ready_event_invalid", "岗位画像就绪事件与岗位版本不一致", status_code=409)

    latest_screening_run_id = (
        select(WorkflowRun.workflow_run_id)
        .where(
            WorkflowRun.application_id == Application.application_id,
            WorkflowRun.workflow_type == "scoring_workflow",
        )
        .order_by(WorkflowRun.started_at.desc(), WorkflowRun.workflow_run_id.desc())
        .limit(1)
        .correlate(Application)
        .scalar_subquery()
    )
    blocked_by_empty_profile = exists(
        select(WorkflowStepCheckpoint.checkpoint_id)
        .join(
            WorkflowRun,
            WorkflowRun.workflow_run_id == WorkflowStepCheckpoint.workflow_run_id,
        )
        .where(
            WorkflowRun.workflow_run_id == latest_screening_run_id,
            WorkflowRun.workflow_type == "scoring_workflow",
            WorkflowRun.status == "blocked",
            WorkflowStepCheckpoint.step_name == "freeze_screening_sources",
            WorkflowStepCheckpoint.last_error_code.in_((
                "screening_job_capabilities_missing",
                "screening_job_capability_coverage_missing",
            )),
        )
    )
    application_ids = list(db.scalars(
        select(Application.application_id).where(
            Application.job_id == job_id,
            Application.jd_version_id == jd_version_id,
            or_(
                Application.status == WAITING_JOB_PROFILE_STATUS,
                and_(
                    Application.status == "screening_failed",
                    or_(
                        blocked_by_empty_profile,
                        Application.recovery_code == "screening_job_profile_required",
                    ),
                ),
            ),
            Application.deleted_at.is_(None),
        )
    ))
    if not application_ids:
        return []

    released: list[str] = []
    for application_id in application_ids:
        try:
            # 每个申请使用独立保存点。这里失败时只回滚本申请的画像绑定、状态迁移
            # 和任务入队；JobVersion 已就绪事实及其他申请的成功结果继续保留。
            with db.begin_nested():
                application = db.scalar(
                    select(Application)
                    .where(Application.application_id == application_id)
                    .with_for_update()
                )
                if application is None or application.status not in {
                    WAITING_JOB_PROFILE_STATUS,
                    "screening_failed",
                }:
                    continue
                if application.status == WAITING_JOB_PROFILE_STATUS:
                    _bind_profile_and_enqueue(
                        db,
                        application=application,
                        profile=profile,
                        actor=actor,
                        source_key=source_key,
                    )
                else:
                    # 该申请已经完成简历处理和硬筛，只因旧空画像在 V1 冻结来源处
                    # 阻塞。改绑新画像后直接重启 V1，不能把整条上传链路再跑一遍。
                    application.job_profile_id = profile.job_profile_id
                    enqueue_screening_retry_after_job_profile(
                        db,
                        user=actor,
                        application=application,
                        source_key=source_key,
                    )
                    clear_application_recovery(application)
            released.append(application_id)
        except Exception as error:
            logger.exception(
                "application_initial_assessment_release_failed",
                extra={"application_id": application_id, "job_profile_id": job_profile_id},
            )
            # 保存点已回滚，必须在外层事务重新加载 Application 后写恢复事实。
            # 这条失败不会阻止同岗位其他申请继续释放。
            failed_application = db.get(Application, application_id)
            if failed_application is not None:
                set_application_recovery(
                    failed_application,
                    ApplicationRecoveryCode.INITIAL_ASSESSMENT_RELEASE_RETRYABLE,
                    context={
                        "failedStep": "release_initial_assessment",
                        "jobProfileId": job_profile_id,
                        "sourceKey": source_key,
                        "errorCode": str(
                            getattr(error, "code", None)
                            or getattr(error, "error_code", None)
                            or type(error).__name__
                        ),
                    },
                )
    return released


def retry_initial_assessment_for_application(
    db: Session,
    *,
    application: Application,
    actor: User,
    source_key: str,
) -> str:
    """只恢复一个 Application 的画像绑定和首个评估任务。"""

    if application.status not in {
        WAITING_JOB_PROFILE_STATUS,
        "hard_screening_pending",
        "submitted",
        "screening_failed",
    }:
        raise BusinessError(
            "initial_assessment_retry_state_invalid",
            "当前申请不需要重新启动初步筛选",
            status_code=409,
        )
    version = db.get(JobVersionRecord, application.jd_version_id)
    profile_id = str(version.active_job_profile_id or "") if version is not None else ""
    profile = db.get(JobRequirementProfileRecord, profile_id) if profile_id else None
    readiness = evaluate_job_profile_record(
        profile,
        job_id=application.job_id,
        jd_version_id=application.jd_version_id,
    )
    if version is None or not readiness.ready:
        raise BusinessError(
            "initial_assessment_job_profile_not_ready",
            "岗位画像尚未就绪，请先到岗位管理完成岗位画像",
            status_code=409,
        )

    if application.status == WAITING_JOB_PROFILE_STATUS:
        runs = _bind_profile_and_enqueue(
            db,
            application=application,
            profile=profile,
            actor=actor,
            source_key=source_key,
        )
    elif application.status == "hard_screening_pending":
        if application.job_profile_id != profile.job_profile_id:
            application.job_profile_id = profile.job_profile_id
        runs = enqueue_initial_assessment(
            db,
            user=actor,
            application_ids=[application.application_id],
            source_key=source_key,
        )
    elif application.status == "submitted":
        existing_v1 = db.scalar(
            select(WorkflowRun.workflow_run_id).where(
                WorkflowRun.application_id == application.application_id,
                WorkflowRun.workflow_type == "scoring_workflow",
            ).limit(1)
        )
        if existing_v1 is not None:
            raise BusinessError(
                "initial_assessment_already_started",
                "初步筛选任务已经启动，请刷新页面查看最新状态",
                status_code=409,
            )
        # submitted 表示硬筛已完成或已明确跳过。这里只补建缺失的 V1，
        # 不能重新运行硬筛，也不能将恢复动作解释成 screening.run 职责。
        runs = enqueue_initial_assessment(
            db,
            user=actor,
            application_ids=[application.application_id],
            source_key=source_key,
        )
    else:
        application.job_profile_id = profile.job_profile_id
        runs = [enqueue_screening_retry_after_job_profile(
            db,
            user=actor,
            application=application,
            source_key=source_key,
        )]
    if not runs:
        raise BusinessError(
            "initial_assessment_not_enqueued",
            "初步筛选任务未能创建，请刷新后重试",
            status_code=409,
        )
    clear_application_recovery(application)
    return runs[0].workflow_run_id


def _bind_profile_and_enqueue(
    db: Session,
    *,
    application: Application,
    profile: JobRequirementProfileRecord,
    actor: User,
    source_key: str,
) -> list:
    """在调用方事务中原子绑定画像、迁移 Application 并创建首个评估任务。"""

    now = _now()
    application.job_profile_id = profile.job_profile_id
    transition = ApplicationProcessService.plan_transition(
        application,
        action="job_profile_ready",
    )
    ApplicationProcessService.apply_transition(application, transition, now=now, owner="系统")
    # 岗位画像已就绪由外键、Application 主状态和 StageHistory 共同表达；
    # 禁止向 payload 再写一份容易滞留的过程状态。
    db.add(
        StageHistory(
            stage_history_id=f"SH_{uuid.uuid4().hex[:12].upper()}",
            application_id=application.application_id,
            actor_role=actor.role,
            actor_name=actor.display_name,
            action=transition.action,
            from_status=transition.from_status,
            to_status=transition.to_status,
            note="岗位能力画像已完成，申请进入初步筛选队列。",
            effective_at=now,
            business_timezone="Asia/Shanghai",
        )
    )
    runs = enqueue_initial_assessment(
        db,
        user=actor,
        application_ids=[application.application_id],
        source_key=source_key,
    )
    if not runs:
        raise RuntimeError("initial_assessment_not_enqueued")
    clear_application_recovery(application)
    return runs


def apply_candidate_resume_rebuild(
    db: Session,
    *,
    command: ResumeRebuildRequest,
    actor_id: str,
) -> ResumeRebuildResult:
    """切换 Candidate 所有有效申请采用的简历版本，并入队重评分，不改变招聘主状态。"""
    actor = _actor(db, actor_id)
    submission = db.get(ResumeSubmission, command.resume_submission_id)
    profile = db.get(ResumeProfileRecord, command.resume_profile_id)
    document = db.get(SourceDocument, command.source_document_id)
    if submission is None or profile is None or document is None:
        raise BusinessError("application_rebuild_reference_missing", "简历重建来源不完整", status_code=409)
    if submission.candidate_id != command.candidate_id or profile.candidate_id != command.candidate_id:
        raise BusinessError("application_rebuild_candidate_mismatch", "简历重建候选人不一致", status_code=409)
    if submission.source_document_id != document.source_document_id:
        raise BusinessError("application_rebuild_document_mismatch", "简历重建文件不一致", status_code=409)
    applications = list(db.scalars(select(Application).where(
        Application.candidate_id == command.candidate_id,
        Application.deleted_at.is_(None),
    )))
    now = _now()
    application_ids: list[str] = []
    for application in applications:
        application.adopted_resume_submission_id = submission.resume_submission_id
        application.source_resume_profile_id = profile.resume_profile_id
        application.updated_at = now
        # 重建后的采用版本已由两个外键更新；后续重评分任务由下方入队逻辑表达，
        # 不再写入容易滞留的 resumeProfileRescoreRequired payload 标记。
        application_ids.append(application.application_id)
    if application_ids:
        enqueue_resume_rebuild_rescoring(
            db,
            user=actor,
            application_ids=application_ids,
            workflow_run_id=command.rebuild_workflow_run_id,
        )
    return ResumeRebuildResult(application_ids=application_ids, reassessment_enqueued=bool(application_ids))
