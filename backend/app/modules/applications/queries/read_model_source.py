from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import case, select
from sqlalchemy.orm import Session

from backend.app.models.entities import (
    Application,
    ApplicationAssessmentVersion,
    Candidate,
    CandidateCapabilityProfileRecord,

    Job,
    JobRequirementProfileRecord,
    ResumeProfileRecord,
    ScoreSnapshot,
    WorkflowArtifact,
    WorkflowRun,
)
from backend.app.shared.errors import BusinessError


@dataclass(frozen=True)
class ApplicationReadSource:
    application: Application
    candidate: Candidate | None
    job: Job | None
    score_snapshots: dict[str, ScoreSnapshot]
    current_score_snapshot: ScoreSnapshot | None
    capability_profile: CandidateCapabilityProfileRecord | None
    # 新链路以 AAV 为评分唯一正式版本；旧快照字段仅服务历史数据兼容。
    assessment_versions: dict[str, ApplicationAssessmentVersion]
    current_assessment_version: ApplicationAssessmentVersion | None

    resume_profile: ResumeProfileRecord | None
    job_profile: JobRequirementProfileRecord | None

    @property
    def assessment_payload(self) -> dict[str, Any] | None:
        """旧卡片的兼容入口；新数据只从 AAV 读取，不再查询已删除表。"""
        return None

class ApplicationReadModelSource:
    """Single read source shared by application-stage page builders."""

    def __init__(self, db: Session) -> None:
        self.db = db

    @staticmethod
    def latest_score_scalar(application_id_column):
        """列表与任务投影统一读取最新正式 AAV；旧 ScoreSnapshot 不再参与新数据排序。"""
        stage_rank = case(
            (ApplicationAssessmentVersion.stage == "after_second_interview", 3),
            (ApplicationAssessmentVersion.stage == "after_first_interview", 2),
            (ApplicationAssessmentVersion.stage == "screening", 1),
            else_=0,
        )
        score = ApplicationAssessmentVersion.core_result_json["score_result"]["total"].as_float()
        return (
            select(score)
            .where(
                ApplicationAssessmentVersion.application_id == application_id_column,
                ApplicationAssessmentVersion.published_at.is_not(None),
            )
            .order_by(
                stage_rank.desc(),
                ApplicationAssessmentVersion.version.desc(),
                ApplicationAssessmentVersion.created_at.desc(),
            )
            .limit(1)
            .scalar_subquery()
        )

    def load(self, application_id: str) -> ApplicationReadSource:
        application = self.db.get(Application, application_id)
        if application is None:
            raise BusinessError(
                "application_not_found",
                "未找到候选申请",
                status_code=404,
            )
        score_rows = self.db.scalars(
            select(ScoreSnapshot)
            .where(ScoreSnapshot.application_id == application_id)
            .order_by(
                ScoreSnapshot.version.desc(),
                ScoreSnapshot.created_at.desc(),
            )
        ).all()
        score_snapshots: dict[str, ScoreSnapshot] = {}
        for row in score_rows:
            score_snapshots.setdefault(row.stage, row)
        assessment_rows = self.db.scalars(
            select(ApplicationAssessmentVersion)
            .where(
                ApplicationAssessmentVersion.application_id == application_id,
                ApplicationAssessmentVersion.published_at.is_not(None),
            )
            .order_by(
                ApplicationAssessmentVersion.version.desc(),
                ApplicationAssessmentVersion.created_at.desc(),
            )
        ).all()
        assessment_versions: dict[str, ApplicationAssessmentVersion] = {}
        for row in assessment_rows:
            assessment_versions.setdefault(row.stage, row)
        stage_rank = {"screening": 1, "after_first_interview": 2, "after_second_interview": 3}
        current_assessment_version = max(
            assessment_rows,
            key=lambda row: (stage_rank.get(row.stage, 0), row.version, row.created_at),
            default=None,
        )
        capability_profile = self.db.scalars(
            select(CandidateCapabilityProfileRecord)
            .where(CandidateCapabilityProfileRecord.application_id == application_id)
            .order_by(
                CandidateCapabilityProfileRecord.version.desc(),
                CandidateCapabilityProfileRecord.created_at.desc(),
            )
        ).first()

        profile_resume_id = (
            current_assessment_version.resume_profile_id if current_assessment_version is not None
            else capability_profile.resume_profile_id if capability_profile is not None else None
        )
        profile_job_id = (
            current_assessment_version.job_profile_id if current_assessment_version is not None
            else capability_profile.job_profile_id if capability_profile is not None else None
        )
        resume_profile = self.db.get(ResumeProfileRecord, profile_resume_id) if profile_resume_id else None
        job_profile = self.db.get(JobRequirementProfileRecord, profile_job_id) if profile_job_id else None
        return ApplicationReadSource(
            application=application,
            candidate=self.db.get(Candidate, application.candidate_id),
            job=self.db.get(Job, application.job_id),
            score_snapshots=score_snapshots,
            current_score_snapshot=score_rows[0] if score_rows else None,
            capability_profile=capability_profile,
            assessment_versions=assessment_versions,
            current_assessment_version=current_assessment_version,

            resume_profile=resume_profile,
            job_profile=job_profile,
        )
    def latest_artifact(
        self,
        application_id: str,
        artifact_type: str,
    ) -> WorkflowArtifact | None:
        return self.db.scalars(
            select(WorkflowArtifact)
            .where(
                WorkflowArtifact.application_id == application_id,
                WorkflowArtifact.artifact_type == artifact_type,
            )
            .order_by(
                WorkflowArtifact.created_at.desc(),
                WorkflowArtifact.artifact_id.desc(),
            )
        ).first()

    def latest_workflow_run(
        self,
        application_id: str,
        workflow_type: str,
    ) -> WorkflowRun | None:
        return self.db.scalars(
            select(WorkflowRun)
            .where(
                WorkflowRun.application_id == application_id,
                WorkflowRun.workflow_type == workflow_type,
            )
            .order_by(
                # 重试任务在 Worker 领取前 started_at 为空；按更新时间/创建时间
                # 选择最新运行，避免 pending 的 V2/V3 重试被旧 completed 任务遮住。
                WorkflowRun.updated_at.desc(),
                WorkflowRun.created_at.desc(),
                WorkflowRun.workflow_run_id.desc(),
            )
        ).first()

    @staticmethod
    def score_snapshot_view(
        source: ApplicationReadSource,
        stage: str,
    ) -> dict[str, Any] | None:
        version = source.assessment_versions.get(stage)
        if version is not None:
            score = dict(version.core_result_json or {}).get("score_result") or {}
            return {
                "stage": version.stage,
                "score": score.get("total"),
                "version": version.version,
                "scoreSnapshotId": version.assessment_version_id,
                "assessmentVersionId": version.assessment_version_id,
                "jobCapabilityFitScore": score.get("job_fit"),
                "resumeExperienceScore": score.get("experience"),
                "educationBackgroundScore": score.get("education"),
                "scoreChanges": dict(version.core_result_json or {}).get("score_changes") or [],
                "capabilityChanges": dict(version.core_result_json or {}).get("capability_changes") or [],
            }
        row = source.score_snapshots.get(stage)
        if row is None:
            return None
        return {
            **dict(row.result_json or {}),
            "stage": row.stage,
            "score": row.score,
            "version": row.version,
            "scoreSnapshotId": row.score_snapshot_id,
        }
