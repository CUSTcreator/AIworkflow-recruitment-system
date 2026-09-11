"""一面后评估完成度门禁。

Application 已进入 ``hr_second_review`` 只表示面试官已作出一面决定；
HR 进入二面审核前仍必须确认 V2 已正式发布。该规则集中在这里，
避免路由、页面和命令各自根据 WorkflowRun 猜测可否继续。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.entities import Application, ApplicationAssessmentVersion, WorkflowRun
from backend.app.shared.errors import BusinessError


class FirstAssessmentReadinessService:
    """判断一面后正式评估（V2）是否已经可供 HR 使用。"""

    def __init__(self, db: Session) -> None:
        self.db = db

    def latest_run(self, application: Application) -> WorkflowRun | None:
        return self.db.scalars(
            select(WorkflowRun)
            .where(
                WorkflowRun.application_id == application.application_id,
                WorkflowRun.workflow_type == "post_first_scoring_workflow",
            )
            .order_by(WorkflowRun.created_at.desc(), WorkflowRun.workflow_run_id.desc())
        ).first()

    def has_blocked_recovery_view(self, application: Application) -> bool:
        """V2 阻塞时允许打开只读审核页处理输入，但不代表可以推进二面。"""

        latest_run = self.latest_run(application)
        return latest_run is not None and latest_run.status == "blocked"

    def require_ready_for_second_review(self, application: Application) -> None:
        """未发布 V2 时拒绝进入或操作二面审核，错误码可直接投影到前端提示。"""
        latest_run = self.latest_run(application)
        if latest_run is not None and latest_run.status in {"pending", "running"}:
            raise BusinessError(
                "first_assessment_processing",
                "一面后评估正在生成，请等待完成后再进入二面审核。",
                status_code=409,
            )
        if latest_run is not None and latest_run.status == "failed":
            raise BusinessError(
                "first_assessment_failed",
                "一面后评估生成失败，请重新计算一面后评估后再进入二面审核。",
                status_code=409,
            )
        published = self.db.scalars(
            select(ApplicationAssessmentVersion)
            .where(
                ApplicationAssessmentVersion.application_id == application.application_id,
                ApplicationAssessmentVersion.stage == "after_first_interview",
                ApplicationAssessmentVersion.published_at.is_not(None),
            )
            .order_by(ApplicationAssessmentVersion.version.desc(), ApplicationAssessmentVersion.created_at.desc())
        ).first()
        if published is None:
            raise BusinessError(
                "first_assessment_missing",
                "尚未生成一面后正式评估，暂不能进入二面审核。",
                status_code=409,
            )
