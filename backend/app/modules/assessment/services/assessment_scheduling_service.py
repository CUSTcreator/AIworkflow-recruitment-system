"""评估调度服务：按岗位规则安排硬筛或初筛评分任务。"""
from __future__ import annotations

from sqlalchemy.orm import Session

from datetime import UTC, datetime
import uuid
from backend.app.models.entities import Application, StageHistory, User, WorkflowRun
from backend.app.modules.applications.public import ApplicationProcessService
from backend.app.modules.assessment.hard_screening.hard_screening_service import HardScreeningService
from backend.app.modules.assessment.commands.scoring_request_commands import ScoringRequestCommands


def enqueue_initial_assessment(
    db: Session,
    *,
    user: User,
    application_ids: list[str],
    source_key: str,
) -> list[WorkflowRun]:
    """为新创建的岗位申请安排第一轮评估任务。

    这是 candidate intake 和 assessment 的边界：这里仅创建后续 WorkflowRun，
    不直接执行硬筛或评分。若岗位配置了硬筛规则，先进入 hard_screening_workflow；
    否则直接进入 scoring_workflow。
    """
    hard_screening = HardScreeningService(db)
    scoring = ScoringRequestCommands(db)
    runs: list[WorkflowRun] = []
    for application_id in application_ids:
        app = db.get(Application, application_id)
        if app is None:
            continue
        if app.status == "submitted":
            # submitted 已越过硬筛边界；该状态下的调度只能创建 V1。否则岗位
            # 配有硬筛规则时，恢复缺失 V1 会错误地再次执行已经完成的硬筛。
            result = scoring.enqueue_in_transaction(
                app=app,
                user=user,
                input_json={"sourceKey": source_key},
                source="candidate_routing",
            )
            runs.append(result.workflow_run)
            continue
        policy = hard_screening.latest_policy(
            app.job_id,
            enabled_only=True,
            jd_version_id=app.jd_version_id,
        )
        if policy is not None and policy.enabled and (policy.rules_json or {}).get("items"):
            runs.append(hard_screening.enqueue_for_application(app, user, policy))
            continue
        if app.status == "hard_screening_pending":
            # 未配置有效硬筛规则时，仍显式完成“跳过硬筛”迁移，再进入 V1；不能把
            # 初始状态直接伪装成 submitted。
            now = datetime.now(UTC).replace(tzinfo=None)
            transition = ApplicationProcessService.plan_transition(app, action="skip_hard_screening")
            db.add(StageHistory(
                stage_history_id=f"SH_{uuid.uuid4().hex[:12].upper()}",
                application_id=app.application_id,
                actor_role="system",
                actor_name="系统",
                action=transition.action,
                from_status=transition.from_status,
                to_status=transition.to_status,
                note="岗位未配置有效硬筛规则，跳过硬筛并进入初步筛选评分。",
                effective_at=now,
                business_timezone="Asia/Shanghai",
            ))
            ApplicationProcessService.apply_transition(app, transition, now=now, owner="系统")
        if app.status in {"submitted", "screening_failed"}:
            # 当前函数由 Candidate/Application 的发布事务调用，不能再进入会自行
            # commit 的 HTTP 命令；状态、阶段历史和 WorkflowRun 必须一起提交。
            result = scoring.enqueue_in_transaction(
                app=app,
                user=user,
                input_json={"sourceKey": source_key},
                source="candidate_routing",
            )
            runs.append(result.workflow_run)
    return runs


def enqueue_resume_rebuild_rescoring(
    db: Session,
    *,
    user: User,
    application_ids: list[str],
    workflow_run_id: str,
) -> None:
    scoring = ScoringRequestCommands(db)
    for application_id in application_ids:
        app = db.get(Application, application_id)
        if app is None or app.status not in {"submitted", "screening_failed"}:
            continue
        try:
            # 简历版本切换和每个申请的重评分任务由上层同一短事务提交；不调用
            # 会自行 commit 的公开 HTTP 命令。
            scoring.enqueue_in_transaction(
                app=app,
                user=user,
                input_json={"resume_rebuild_workflow_run_id": workflow_run_id},
                source="candidate_resume_rebuild",
            )
        except Exception as exc:
            if getattr(exc, "code", "") != "screening_already_started":
                raise


def enqueue_screening_retry_after_job_profile(
    db: Session,
    *,
    user: User,
    application: Application,
    source_key: str,
) -> WorkflowRun:
    """画像修复后只重启 V1，不重复简历处理或已经完成的硬筛。"""

    if application.status != "screening_failed":
        raise ValueError("job_profile_screening_retry_state_invalid")
    result = ScoringRequestCommands(db).enqueue_in_transaction(
        app=application,
        user=user,
        input_json={"sourceKey": source_key},
        source="job_profile_ready",
        # 本次失败来源是岗位画像；重新结构化简历既无帮助，也会扩大恢复范围。
        allow_resume_restructuring=False,
    )
    return result.workflow_run
