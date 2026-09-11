"""初筛正式评估版本发布。

一轮初筛只发布一条 ``ApplicationAssessmentVersion``：来源、纯评分核心、规则派生和
展示文案分别保存。``WorkflowArtifact`` 只保留可审计分析包，不承担业务读模型职责。
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.entities import (
    Application, ApplicationAssessmentVersion, InterviewParseResultRecord, InterviewTarget, JobRequirementProfileRecord,
    ResumeProfileRecord, StageHistory, Task, User, WorkflowArtifact, WorkflowRun,
)
from backend.app.modules.applications.public import ApplicationProcessService
from backend.app.modules.applications.application_recovery import (
    ApplicationRecoveryCode,
    clear_application_recovery,
    set_application_recovery,
)
from backend.app.modules.tasks.public import TaskWriteService
from backend.app.infrastructure.database.locking import lock_business_resources
from backend.app.storage.object_store import ObjectStore
from backend.app.modules.assessment.domain.assessment_version import (
    AssessmentCoreResult, AssessmentPresentationResult, AssessmentRuleResult, InterviewTargetUpdate,
)
from backend.app.modules.assessment.domain.result_normalizer import (
    normalize_assessment_core_result,
    normalize_assessment_rule_result,
)
from backend.app.modules.assessment.domain.topology_snapshot import build_v1_topology_snapshot
from backend.app.modules.assessment.services.topology_persistence import (
    V1TopologyPersistenceService,
    build_v1_persistence_plan,
)
from recruitment_ai_core.screening_scoring.result_contracts import (
    PresentationResult, RuleDerivedResult, ScreeningCoreResult,
)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12].upper()}"


def _stable_hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


class AssessmentVersionPublisher:
    """将三个阶段结果和来源版本在一个事务中发布。"""

    def __init__(self, db: Session, store: ObjectStore | None = None) -> None:
        self.db = db
        self.store = store or ObjectStore()

    def _upsert_target_updates(self, application_id: str, updates: list[InterviewTargetUpdate]) -> None:
        """仅供 V1 初筛首次物化 Target；V2/V3 必须走 PostInterviewAssessmentPublisher。"""
        existing = {
            row.interview_target_id: row
            for row in self.db.scalars(select(InterviewTarget).where(InterviewTarget.application_id == application_id)).all()
        }
        for update in updates:
            row = existing.get(update.interview_target_id)
            values = {
                "purpose": update.purpose, "target_type": update.target_type, "target_id": update.target_id,
                "title": update.title, "verification_goal": update.verification_goal, "trigger_code": update.trigger_code,
                "status": update.status.value, "stage_created": update.stage_created.value,
                "resolved_stage": update.resolved_stage.value if update.resolved_stage else None,
                "resolution_note": update.resolution_note, "source_result_ids": list(update.source_result_ids),
                "evidence_ids": list(update.evidence_ids), "updated_at": datetime.now(UTC).replace(tzinfo=None),
            }
            if row is None:
                self.db.add(InterviewTarget(application_id=application_id, interview_target_id=update.interview_target_id, **values))
            else:
                for key, value in values.items():
                    setattr(row, key, value)
    @staticmethod
    def stage_screening_analysis_bundle(
        *, application_id: str, workflow_run_id: str,
        core: ScreeningCoreResult, rule: RuleDerivedResult,
        presentation: PresentationResult,
        hard_screening_source: Mapping[str, Any] | None = None,
    ) -> dict[str, str]:
        """在发布 Step 的 handler 中上传初筛审计包，不能位于 SQL 事务。"""
        sections = AssessmentVersionPublisher._sections(
            core, rule, presentation, hard_screening_source=hard_screening_source,
        )
        bundle = {
            "schemaVersion": "screening_analysis_bundle_v1",
            "source": sections["source"], "coreResult": sections["core"],
            "ruleResult": sections["rule"], "presentation": sections["presentation"],
        }
        key = f"applications/{application_id}/workflows/{workflow_run_id}/analysis_bundle.json"
        object_ref, sha256 = ObjectStore().put_json(key, bundle)
        return {"objectRef": object_ref, "objectSha256": sha256}

    def publish_screening(
        self, user: User, app: Application, *, core: ScreeningCoreResult,
        rule: RuleDerivedResult, presentation: PresentationResult,
        workflow_run: WorkflowRun, analysis_bundle_ref: Mapping[str, str],
        hard_screening_source: Mapping[str, Any] | None = None,
    ) -> ApplicationAssessmentVersion:
        # 同一 Application 的同一评估阶段只能有一个发布者；锁住 Application 行，
        # 保证版本分配与发布在一个事务内串行完成。
        locked = lock_business_resources(self.db, application_id=app.application_id)
        app = locked["application"]
        rebuild_published_assessment = bool(
            (workflow_run.input_json or {}).get("rebuild_published_assessment")
        )
        if not rebuild_published_assessment and app.status != "screening_running":
            raise RuntimeError(f"scoring_status_changed:{app.status}")

        sections = self._sections(
            core, rule, presentation, hard_screening_source=hard_screening_source,
        )
        # V1 发布时冻结“允许被 V2/V3 面评更新的能力图”。这一步只投影既有评分结果，
        # 不重新计算、更不补造新的证据配对；因此可与 AAV 在同一短事务原子发布。
        topology = build_v1_topology_snapshot(sections["core"], resume_profile=dict(core.resume_profile or {}))
        topology_plan = build_v1_persistence_plan(topology)
        resume_id = sections["source"]["resumeProfileId"]
        job_id = sections["source"]["jobProfileId"]
        if self.db.get(ResumeProfileRecord, resume_id) is None:
            raise RuntimeError("screening_resume_profile_not_published")
        if self.db.get(JobRequirementProfileRecord, job_id) is None:
            raise RuntimeError("screening_job_profile_not_published")

        # 分析包已由发布 handler 在事务外上传；本事务只登记引用，绝不访问对象存储。
        object_ref = str(analysis_bundle_ref.get("objectRef") or "")
        sha256 = str(analysis_bundle_ref.get("objectSha256") or "")
        if not object_ref or not sha256:
            raise RuntimeError("screening_analysis_bundle_stage_missing")
        analysis_artifact = self.db.scalar(
            select(WorkflowArtifact).where(
                WorkflowArtifact.workflow_run_id == workflow_run.workflow_run_id,
                WorkflowArtifact.artifact_type == "analysis_bundle",
                WorkflowArtifact.version == 1,
            )
        )
        if analysis_artifact is None:
            analysis_artifact = WorkflowArtifact(
                artifact_id=_id("ART"), workflow_run_id=workflow_run.workflow_run_id,
                application_id=app.application_id, artifact_type="analysis_bundle",
                artifact_json=None, object_ref=object_ref, object_sha256=sha256, version=1,
            )
            self.db.add(analysis_artifact)
        else:
            analysis_artifact.object_ref = object_ref
            analysis_artifact.object_sha256 = sha256

        previous = self.db.scalars(select(ApplicationAssessmentVersion).where(
            ApplicationAssessmentVersion.application_id == app.application_id,
            ApplicationAssessmentVersion.stage == "screening",
        ).order_by(ApplicationAssessmentVersion.version.desc(), ApplicationAssessmentVersion.created_at.desc())).first()
        now = datetime.now(UTC).replace(tzinfo=None)
        assessment = ApplicationAssessmentVersion(
            assessment_version_id=_id("ASV"), application_id=app.application_id,
            stage="screening", version=(previous.version + 1) if previous else 1,
            previous_assessment_version_id=previous.assessment_version_id if previous else None,
            resume_profile_id=resume_id, job_profile_id=job_id,
            source_interview_parse_result_id=None,
            source_json={
                **sections["source"],
                "topologyDefinitionSchemaVersion": topology_plan.definition_schema_version,
                "topologyStateSchemaVersion": topology_plan.state_schema_version,
                "topologyDefinitionHash": topology_plan.structure_hash,
                "topologyStateHash": topology_plan.state_hash,
            },
            core_result_json=sections["core"], rule_result_json=sections["rule"],
            presentation_json=sections["presentation"], published_at=now,
        )
        self.db.add(assessment)
        # 评估版本是拓扑定义的父记录，先 flush 确保后续拓扑外键可引用。
        self.db.flush()
        # Definition、Snapshot
        # Definition、Snapshot、NodeState 与 AAV 同属一个发布事务；任一行写入失败时
        # 由 StepRunner 统一回滚，不能留下“有初筛版本但没有冻结拓扑”的半成品。
        V1TopologyPersistenceService(self.db, id_factory=_id).persist(
            assessment=assessment,
            application_id=app.application_id,
            topology=topology,
            plan=topology_plan,
        )
        self._upsert_target_updates(
            app.application_id,
            _screening_target_updates(rule.target_updates),
        )
        if not rebuild_published_assessment:
            transition = ApplicationProcessService.plan_transition(app, action="complete_scoring")
            done = transition.to_status
            self.db.add(StageHistory(
                stage_history_id=_id("SH"), application_id=app.application_id,
                actor_role=user.role, actor_name=user.display_name, action="complete_scoring",
                from_status="screening_running", to_status=done,
                note="初步筛选评分任务已完成。", effective_at=now,
                business_timezone="Asia/Shanghai",
            ))
            TaskWriteService(self.db).complete(application_id=app.application_id, task_type="run_scoring")
            # 岗位可先接收候选人、后补齐招聘人员配置。初筛发布不能因为尚未指派
            # 部门招聘人而回滚；待岗位配置完成时由岗位管理写路径补建这项待办。
            if app.assigned_first_interviewer:
                TaskWriteService(self.db).ensure_pending(
                    application_id=app.application_id, task_type="department_review", title="部门初步筛选审核",
                    assignee_user_id=app.assigned_first_interviewer, assignee_role="department_recruiter",
                )
            ApplicationProcessService.apply_transition(app, transition, now=now, owner="林经理")
        # V1 来源重建只是修复下游 V2/V3 的输入，不能在这里清掉恢复上下文；
        # 调用方仍需要根据原恢复状态继续重新计算对应的面后评估。
        if not rebuild_published_assessment:
            clear_application_recovery(app)
        self.db.flush()
        return assessment

    def record_screening_failure(
        self,
        user: User,
        app: Application,
        error: Exception,
        *,
        error_code: str = "",
        error_category: str = "",
        step_name: str = "",
        recovery_action: str = "",
        blocked: bool = False,
        workflow_run_id: str = "",
    ) -> None:
        """失败不伪造评估版本，只记录流程状态与失败原因。"""
        if app.status != "screening_running":
            return
        now = datetime.now(UTC).replace(tzinfo=None)
        transition = ApplicationProcessService.plan_transition(app, action="fail_scoring")
        self.db.add(StageHistory(
            stage_history_id=_id("SH"), application_id=app.application_id,
            actor_role=user.role, actor_name=user.display_name, action=transition.action,
            from_status=transition.from_status, to_status=transition.to_status,
            note=f"初步筛选处理失败：{str(error)[:200]}", effective_at=now,
            business_timezone="Asia/Shanghai",
        ))
        ApplicationProcessService.apply_transition(app, transition, now=now, owner="HR 王敏")
        set_application_recovery(
            app,
            self._screening_recovery_code(error_code=error_code, step_name=step_name),
            context={
                "workflowRunId": workflow_run_id or None,
                "failedStep": step_name or None,
                "errorCode": error_code or None,
                "errorCategory": error_category or None,
                "runtimeRecoveryAction": recovery_action or None,
                "blocked": blocked,
            },
        )

    @staticmethod
    def _screening_recovery_code(
        *, error_code: str, step_name: str,
    ) -> ApplicationRecoveryCode:
        """将运行时错误翻译成业务恢复边界，而不是把异常文本暴露给页面。"""

        code = str(error_code or "").casefold()
        if code in {
            "application_frozen_resume_profile_missing",
            "screening_resume_profile_candidate_mismatch",
            "screening_resume_profile_not_published",
        } or "resume_profile" in code:
            return ApplicationRecoveryCode.SCREENING_RESUME_SOURCE_REQUIRED
        if code in {
            "application_frozen_job_profile_missing",
            "application_frozen_job_version_missing",
            "screening_job_capabilities_missing",
            "screening_job_capability_coverage_missing",
            "screening_job_profile_binding_mismatch",
            "screening_job_version_binding_mismatch",
            "screening_job_profile_not_published",
        } or "job_profile" in code or "job_version" in code:
            return ApplicationRecoveryCode.SCREENING_JOB_PROFILE_REQUIRED
        if code == "screening_preset_model_unavailable" or "preset_model" in code:
            return ApplicationRecoveryCode.SCREENING_MODEL_CONFIGURATION_REQUIRED
        if code in {
            "screening_source_projection_invalid",
            "screening_result_profile_reference_missing",
        }:
            return ApplicationRecoveryCode.SCREENING_SOURCE_REVIEW_REQUIRED
        if step_name == "publish_screening_assessment":
            return ApplicationRecoveryCode.SCREENING_PUBLISH_RETRYABLE
        return ApplicationRecoveryCode.SCREENING_RETRYABLE

    @staticmethod
    def _sections(
        core: ScreeningCoreResult,
        rule: RuleDerivedResult,
        presentation: PresentationResult,
        *,
        hard_screening_source: Mapping[str, Any] | None = None,
    ) -> dict[str, dict[str, Any]]:
        core_data = asdict(core)
        rule_data = asdict(rule)
        presentation_data = asdict(presentation)
        resume = dict(core.resume_profile)
        job = dict(core.job_profile)
        resume_id = str(resume.get("resume_profile_version_id") or "")
        job_id = str(job.get("job_profile_version_id") or "")
        if not resume_id or not job_id:
            raise RuntimeError("screening_result_profile_reference_missing")
        source = {
            "schemaVersion": "screening_assessment_source_v1",
            "resumeProfileId": resume_id, "jobProfileId": job_id,
            "rankingDatasetVersion": (core.capability_graph.get("source_inputs") or {}).get("ranking_dataset_version"),
            "algorithmVersions": dict(core.capability_graph.get("algorithm_versions") or {}),
        }
        # 硬筛引用和结论也属于本版冻结输入，并随分析包与 AAV 一起留存。
        if hard_screening_source:
            source["hardScreening"] = dict(hard_screening_source)
        source["sourceInputHash"] = _stable_hash({**source, "resume": resume_id, "job": job_id})
        # V1 算法内存结果保留 resume/job 便于本轮计算；正式 AAV 只保存统一
        # AssessmentCoreResult。来源画像始终由 source_json 与外键定位，不复制进核心结果。
        normalized_core = normalize_assessment_core_result(core_data)
        AssessmentVersionPublisher._assert_screening_score_contract(normalized_core)
        return {
            "source": source,
            "core": normalized_core,
            "rule": normalize_assessment_rule_result(rule_data),
            "presentation": {"schemaVersion": "screening_presentation_v1", **presentation_data},
        }

    @staticmethod
    def _assert_screening_score_contract(core: Mapping[str, Any]) -> None:
        """初筛发布前确认页面所需四项汇总分均已从评分核心正确归一化。"""
        score = dict(core.get("score_result") or {})
        missing = [
            name for name in ("total", "job_fit", "experience", "education")
            if not isinstance(score.get(name), (int, float))
        ]
        if missing:
            raise RuntimeError("screening_score_dimensions_missing:" + ",".join(missing))

# 初筛规则结果保留完整的可追溯快照；写入 InterviewTarget 表时只保留该实体契约允许的字段。
def _screening_target_updates(values: list[Mapping[str, Any]]) -> list[InterviewTargetUpdate]:
    allowed_fields = set(InterviewTargetUpdate.model_fields)
    updates: list[InterviewTargetUpdate] = []
    for value in values:
        if not isinstance(value, Mapping):
            raise RuntimeError("screening_target_update_invalid")
        updates.append(
            InterviewTargetUpdate.model_validate(
                {key: item for key, item in value.items() if key in allowed_fields}
            )
        )
    return updates

def _model_json(value: Any) -> dict[str, Any]:
    """统一把 dataclass/Pydantic 结果转成 JSON 安全的普通字典。"""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "as_dict"):
        return value.as_dict()
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    return dict(value) if isinstance(value, dict) else {}


def _completion_action(stage: str, body: Mapping[str, Any]) -> str:
    action = str(body.get("_workflow_action") or "")
    decision = str(body.get("decision") or "")
    if stage == "after_first_interview":
        if action == "submit_first_feedback":
            return "submit_first_feedback"
        if action == "complete_first_interview" and decision in {"pass", "reject"}:
            return f"complete_first_interview_{decision}"
    if stage == "after_second_interview":
        if action == "submit_second_feedback":
            return "submit_second_feedback"
        if action == "complete_second_interview" and decision in {"pass", "reject"}:
            return f"complete_second_interview_{decision}"
    raise RuntimeError(f"post_interview_completion_action_invalid:{stage}:{action}:{decision}")
