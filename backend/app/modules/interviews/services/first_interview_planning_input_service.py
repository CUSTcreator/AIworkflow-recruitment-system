"""一面题单冻结输入装配。

这里负责 ORM 读取、版本锁定和输入快照；算法包只接收普通 dataclass，禁止直接访问数据库。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.entities import (
    Application,
    ApplicationAssessmentVersion,
    InterviewTarget,
    Job,
    JobRequirementProfileRecord,
    ResumeProfileRecord,
)
from backend.app.modules.interview_guides.public import InterviewGuideTemplateService
from backend.app.modules.interviews.services.interview_persistence_views import interview_target_view
from backend.app.shared.errors import BusinessError
from recruitment_ai_core.first_interview_planning.contracts import (
    FirstInterviewPlanningConfig,
    FirstInterviewPlanningInput,
)
from recruitment_ai_core.first_interview_planning.policy import POLICY_VERSION


class FirstInterviewPlanningInputService:
    """将 V1、Open Target 与模板冻结为题单唯一输入。"""

    def __init__(self, db: Session) -> None:
        self.db = db

    def load(
        self,
        app: Application,
        body: dict[str, Any] | None = None,
    ) -> tuple[FirstInterviewPlanningInput, ApplicationAssessmentVersion, dict[str, Any] | None]:
        # 1. 【读取对象：ApplicationAssessmentVersion】锁定最新已发布 V1，后续流程不得改读其他评分版本。
        assessment = self._latest_screening_assessment(app.application_id)
        if assessment is None or assessment.published_at is None:
            raise BusinessError(
                "first_interview_source_assessment_missing",
                "尚未找到已发布的初步筛选评估结果，不能生成一面题单",
                status_code=409,
            )
        # 2. 【读取对象：Job】只为取得冻结岗位画像的展示名称和通用题单模板。
        job = self.db.get(Job, app.job_id)
        if job is None:
            raise BusinessError("application_job_missing", "候选申请缺少岗位信息", status_code=409)
        payload = body if isinstance(body, dict) else {}
        config = self._config(payload)
        # 3. 【读取对象：InterviewTarget】按创建顺序读取当前全部 Open Target；不截断、不按页面重新排序。
        targets = self._open_interview_targets(app.application_id)
        if len(targets) > config.max_open_interview_targets:
            raise BusinessError(
                "first_interview_open_target_limit_exceeded",
                "当前待确认目标超过系统允许的 5 个上限，请先修正初步筛选目标选择结果",
                status_code=409,
            )
        # 4. 【读取对象：InterviewGuideTemplateVersion】冻结岗位当前可用的通用题单模板版本。
        template = InterviewGuideTemplateService(self.db).resolve_for_job(job)
        # 4.5 【读取对象：ResumeProfileRecord / JobRequirementProfileRecord】AAV 只保存
        # 引用，不把整个画像复制进 core_result_json；因此题单必须沿外键读取 V1 实际冻结的两版画像。
        resume_row = self.db.get(ResumeProfileRecord, assessment.resume_profile_id)
        job_profile_row = self.db.get(JobRequirementProfileRecord, assessment.job_profile_id)
        if resume_row is None or job_profile_row is None:
            raise BusinessError(
                "first_interview_source_profile_missing",
                "初步筛选评估结果引用的简历画像或岗位画像不存在，不能生成不可追溯的题单",
                status_code=409,
            )
        resume_profile = {
            # ResumeProfile 的正式内容已迁移到 profile_json；profile_data 只在历史数据时回退 payload。
            **dict(resume_row.profile_data or {}),
            "resume_profile_id": resume_row.resume_profile_id,
        }
        job_profile = {
            **dict(job_profile_row.profile_json or {}),
            "job_profile_id": job_profile_row.job_profile_id,
        }
        # 5. 【产生对象，仅内存】将 Target 关联的证据投影放入运行时输入；不向数据库写入。
        planning_input = FirstInterviewPlanningInput(
            application_id=app.application_id,
            candidate_id=app.candidate_id,
            job_id=app.job_id,
            job_title=job.title,
            job_profile=job_profile,
            resume_profile=resume_profile,
            evidence_records=_evidence_records(resume_profile),
            interview_targets=targets,
            generation_config=config,
            metadata={
                "source_assessment_version_id": assessment.assessment_version_id,
                "source_assessment_version": assessment.version,
                "source_assessment_hash": (assessment.source_json or {}).get("sourceInputHash"),
                "template": template or {},
                "template_version_id": (template or {}).get("templateVersionId"),
                "planning_policy_version": POLICY_VERSION,
                "llm_config": payload.get("llm_config") if isinstance(payload.get("llm_config"), dict) else {},
            },
        )
        return planning_input, assessment, template

    def load_manual_source(
        self, app: Application,
    ) -> tuple[ApplicationAssessmentVersion, dict[str, Any] | None, Job]:
        """读取人工题纲继续所需的最小来源，不套用自动生成限制。"""

        assessment = self._latest_screening_assessment(app.application_id)
        if assessment is None or assessment.published_at is None:
            raise BusinessError(
                "first_interview_source_assessment_missing",
                "尚未找到已发布的初步筛选评估结果，不能创建可追溯的一面题纲",
                status_code=409,
            )
        job = self.db.get(Job, app.job_id)
        if job is None:
            raise BusinessError("application_job_missing", "候选申请缺少岗位信息", status_code=409)
        return assessment, InterviewGuideTemplateService(self.db).resolve_for_job(job), job

    def _config(self, payload: dict[str, Any]) -> FirstInterviewPlanningConfig:
        config_payload = payload.get("generation_config")
        if config_payload is not None and not isinstance(config_payload, dict):
            raise BusinessError("first_interview_generation_config_invalid", "generation_config 必须是对象", status_code=422)
        try:
            return FirstInterviewPlanningConfig(**(config_payload or {}))
        except (TypeError, ValueError) as exc:
            raise BusinessError("first_interview_generation_config_invalid", f"题单生成配置不合法：{exc}", status_code=422) from exc

    def _latest_screening_assessment(self, application_id: str) -> ApplicationAssessmentVersion | None:
        return self.db.scalars(
            select(ApplicationAssessmentVersion)
            .where(
                ApplicationAssessmentVersion.application_id == application_id,
                ApplicationAssessmentVersion.stage == "screening",
                ApplicationAssessmentVersion.published_at.is_not(None),
            )
            .order_by(ApplicationAssessmentVersion.version.desc(), ApplicationAssessmentVersion.created_at.desc())
        ).first()

    def _open_interview_targets(self, application_id: str) -> list[dict[str, Any]]:
        return [
            interview_target_view(row)
            for row in self.db.scalars(
                select(InterviewTarget)
                .where(InterviewTarget.application_id == application_id, InterviewTarget.status == "open")
                .order_by(InterviewTarget.created_at.asc(), InterviewTarget.id.asc())
            )
        ]


def _dict_list(value: Any) -> list[dict[str, Any]]:
    return [dict(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _mapping(value: Any) -> dict[str, Any]:
    """仅接收 JSON 对象，避免将错误类型悄悄转换为可用的空题单输入。"""
    return dict(value) if isinstance(value, dict) else {}

def _evidence_records(resume_profile: dict[str, Any]) -> list[dict[str, Any]]:
    from recruitment_ai_core.screening_scoring.profile_evidence import nested_source_bullets, nested_work_units

    records: list[dict[str, Any]] = []
    for item in nested_source_bullets(resume_profile):
        records.append({"evidence_id": item.get("source_bullet_id"), "evidence_type": "source_bullet", "raw_text": item.get("raw_text", "")})
    for item in nested_work_units(resume_profile):
        records.append({"evidence_id": item.get("work_unit_id"), "evidence_type": "scorable_work_unit", "raw_text": item.get("raw_text", "")})
    return records
