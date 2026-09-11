"""岗位能力画像的输入冻结、编译与发布服务。

本模块不处理 HTTP，也不直接调度 Worker。它把岗位确认后产生的 JobVersionRecord
作为唯一输入事实，并把算法包返回的画像发布为 JobRequirementProfileRecord。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.infrastructure.workflow_runtime.queue import WorkflowQueue
from backend.app.modules.applications.public import release_applications_waiting_for_job_profile
from backend.app.modules.jobs.contracts import build_job_profile_ready_event
from backend.app.shared.audit import record_audit_event
from backend.app.modules.jobs.profile_readiness import (
    evaluate_job_profile_json,
    evaluate_job_profile_record,
)
from backend.app.modules.jobs.job_process_service import JobProcessService, JobProfileStatus
from backend.app.models.entities import (
    HardScreeningPolicy,
    Job,
    JobRequirementProfileRecord,
    JobVersionRecord,
    User,
    WorkflowRun,
)
from backend.app.shared.errors import BusinessError
from recruitment_ai_core.job_capability import compile_job_profile
from recruitment_ai_core.screening_scoring.resume_experience.preset_models import get_preset_model


WORKFLOW_TYPE = "job_profile_compilation_workflow"
INPUT_SCHEMA_VERSION = "job_profile_compile_input_v1"
PROFILE_STATUS_QUEUED = "queued"
PROFILE_STATUS_PROCESSING = "processing"
PROFILE_STATUS_READY = "ready"
PROFILE_STATUS_FAILED = "failed"


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@dataclass(frozen=True, slots=True)
class JobProfileCompilationInput:
    """岗位画像编译的冻结输入。

    job_id 只表示归属；真正不可变的业务输入是 jd_version_id 对应的 frozen_job_json、
    source_text、source_sha256 和预设能力模型版本。候选人、Application 和任何简历字段
    均不允许进入。
    """

    job_id: str
    jd_version_id: str
    jd_version: int
    source_sha256: str
    source_text: str
    preset_model_id: str
    preset_model_version: str
    algorithm_version: str
    # 结构化 Excel 快照用于生成 JDUnit；source_text 仅用于展示、哈希和兼容旧入口。
    frozen_job_json: dict[str, Any] = field(default_factory=dict)
    # 默认 revision=1 保持首次生成和旧调用兼容。
    profile_revision: int = 1

    def checkpoint_input(self) -> dict[str, Any]:
        """供 StepRunner 计算输入哈希；不放入完整 JD 文本，避免检查点膨胀。"""
        return {
            "schemaVersion": INPUT_SCHEMA_VERSION,
            "jobId": self.job_id,
            "jdVersionId": self.jd_version_id,
            "jdVersion": self.jd_version,
            "profileRevision": self.profile_revision,
            "sourceSha256": self.source_sha256,
            "presetModelId": self.preset_model_id,
            "presetModelVersion": self.preset_model_version,
            "algorithmVersion": self.algorithm_version,
        }


class JobProfileService:
    """围绕一版 JD 管理画像 workflow 的领域操作。"""

    def __init__(self, db: Session) -> None:
        self.db = db

    def enqueue_for_version(
        self,
        version: JobVersionRecord,
        *,
        triggered_by: str,
        force: bool = False,
        preset_model_id: str | None = None,
        preset_model_version: str | None = None,
    ) -> WorkflowRun | None:
        """将指定冻结 JD 放入岗位画像队列。

        首次生成和失败重试均使用同一 JD 版本；ready 后的重新生成使用新的画像
        revision。旧 active_job_profile_id 在新任务成功前始终可被 Application 使用。
        """
        active = self._profile_for_version(version)
        if active is not None and not force:
            JobProcessService.mark_profile(
                version,
                status=JobProfileStatus.READY,
                now=version.profile_completed_at or _now(),
            )
            return None

        next_revision = self._next_profile_revision(version)
        run, _ = WorkflowQueue(self.db).enqueue(
            workflow_type=WORKFLOW_TYPE,
            subject_type="job_version",
            subject_id=version.jd_version_id,
            triggered_by=triggered_by,
            input_json={
                **self._queue_input_for_version(
                    version,
                    profile_revision=next_revision,
                ),
                **({"presetModelId": preset_model_id} if preset_model_id else {}),
                **({"presetModelVersion": preset_model_version} if preset_model_version else {}),
            },
            reuse_active=True,
        )
        # JobVersionRecord.profile_workflow_run_id 受数据库外键约束。队列仅 add()
        # WorkflowRun，不会立即 INSERT；这里必须先 flush，确保新任务行已存在，
        # 再把任务 ID 投影到 JD 版本，避免确认岗位时出现外键冲突并回滚整笔事务。
        self.db.flush()
        version.profile_workflow_run_id = run.workflow_run_id
        JobProcessService.mark_profile(version, status=JobProfileStatus.QUEUED, now=_now())
        return run

    def publish_confirmed_hard_screening_policy(
        self,
        version: JobVersionRecord,
        *,
        actor_id: str,
        rules: list[dict[str, Any]],
    ) -> HardScreeningPolicy:
        """将导入确认页的选择立即发布，画像生成不得再次覆盖或要求确认。"""
        normalized = [
            item
            for rule in rules
            if (item := self._normalize_confirmed_hard_screening_rule(rule)) is not None
        ]
        enabled = any(bool(item.get("enabled", True)) for item in normalized)
        latest = self.db.scalar(
            select(HardScreeningPolicy)
            .where(HardScreeningPolicy.job_id == version.job_id)
            .order_by(HardScreeningPolicy.version.desc())
        )
        latest_meta = dict(latest.rules_json or {}) if latest is not None else {}
        comparable = lambda items: [
            {key: value for key, value in item.items() if key != "rule_id"}
            for item in items
        ]
        if (
            latest is not None
            and str(latest_meta.get("source_jd_version_id") or "") == version.jd_version_id
            and str(latest_meta.get("generation_mode") or "") == "job_import_confirmed"
            and latest.enabled == enabled
            and comparable(list(latest_meta.get("items") or [])) == comparable(normalized)
        ):
            return latest

        policy_version = int(
            self.db.scalar(
                select(func.max(HardScreeningPolicy.version)).where(
                    HardScreeningPolicy.job_id == version.job_id
                )
            )
            or 0
        ) + 1
        policy = HardScreeningPolicy(
            policy_id=f"HSP_{uuid4().hex[:20].upper()}",
            job_id=version.job_id,
            version=policy_version,
            enabled=enabled,
            rules_json={
                "items": normalized,
                "schema_version": "hard_screening_policy_v1",
                "source_jd_version_id": version.jd_version_id,
                "generation_mode": "job_import_confirmed",
                # 即使用户确认不启用任何规则，这次选择本身也已完成确认。
                "generation_status": "confirmed",
            },
            created_by=actor_id,
            created_at=_now(),
        )
        self.db.add(policy)
        self.db.flush()
        actor = self.db.get(User, actor_id)
        if actor is not None:
            record_audit_event(
                self.db,
                actor=actor,
                action="job_document.hard_screening.confirm",
                target_type="job",
                target_id=version.job_id,
                summary=f"确认岗位硬筛条件（版本 {policy_version}）",
                details={
                    "policyId": policy.policy_id,
                    "jdVersionId": version.jd_version_id,
                    "enabled": enabled,
                    "ruleCount": len(normalized),
                },
            )
        return policy

    @staticmethod
    def _normalize_confirmed_hard_screening_rule(
        rule: dict[str, Any],
    ) -> dict[str, Any] | None:
        bindings = {
            "minimum_degree": ("最低学历", "education", "degree_at_least"),
            "minimum_experience_years": ("相关工作经验", "work_experience", "years_at_least"),
            "project_experience": ("相关项目经验", "project_experience", "semantic_match"),
            "required_skill": ("必备技能", "skills", "semantic_match"),
            "certification": ("证书/资质", "certifications", "semantic_match"),
            "custom": ("自定义条件", "full_resume", "semantic_match"),
        }
        criterion_type = str(rule.get("criterion_type") or "").strip()
        binding = bindings.get(criterion_type)
        if binding is None:
            raise BusinessError(
                "hard_screening_rule_type_unsupported",
                "包含不支持的硬筛条件类型，请修改后重新确认",
                status_code=422,
            )
        expected = rule.get("expected_value")
        enabled = bool(rule.get("enabled", True))
        if isinstance(expected, str):
            expected = expected.strip()
        if expected in (None, ""):
            if not enabled:
                return None
            raise BusinessError(
                "hard_screening_rule_value_missing",
                f"{binding[0]}未填写要求，请补充或删除该条件",
                status_code=422,
            )
        if criterion_type == "minimum_experience_years":
            try:
                expected = float(expected)
            except (TypeError, ValueError) as exc:
                raise BusinessError(
                    "hard_screening_rule_value_invalid",
                    "相关工作经验必须填写有效年数",
                    status_code=422,
                ) from exc
            if expected < 0:
                raise BusinessError(
                    "hard_screening_rule_value_invalid",
                    "相关工作经验年数不能小于 0",
                    status_code=422,
                )
        name, source_scope, operator = binding
        return {
            "rule_id": str(rule.get("rule_id") or f"HSRULE_{uuid4().hex[:20].upper()}"),
            "criterion_type": criterion_type,
            "name": name,
            "source_scope": source_scope,
            "operator": operator,
            "expected_value": expected,
            "description": str(rule.get("description") or "").strip(),
            "enabled": enabled,
            "schema_version": "hard_screening_rule_v1",
        }

    def request_profile_run(
        self,
        *,
        job_id: str,
        triggered_by: str,
        mode: str,
    ) -> WorkflowRun:
        """接受业务侧重试/重新生成命令，并只对当前 JD 版本创建任务。"""
        version = self.db.scalar(
            select(JobVersionRecord)
            .where(JobVersionRecord.job_id == job_id)
            .order_by(JobVersionRecord.version.desc())
            .limit(1)
        )
        if version is None:
            raise RuntimeError("job_version_missing")
        status = str(version.profile_status or "queued")
        # 历史版本可能留下 ``ready`` 状态，但其画像内容并不满足 V1
        # 的最小能力集合。读模型会把它投影为“待确认”，这里也必须同步
        # 归一化底层状态，否则用户点击页面提供的“重试”会被旧状态拒绝。
        if status == JobProfileStatus.READY.value:
            active_profile = (
                self.db.get(JobRequirementProfileRecord, version.active_job_profile_id)
                if version.active_job_profile_id
                else None
            )
            readiness = evaluate_job_profile_record(
                active_profile,
                job_id=version.job_id,
                jd_version_id=version.jd_version_id,
            )
            if not readiness.ready:
                status = JobProfileStatus.REVIEW_REQUIRED.value
                JobProcessService.mark_profile(
                    version,
                    status=JobProfileStatus.REVIEW_REQUIRED,
                    now=_now(),
                )
                version.recovery_code = "job_profile_review_required"
                version.recovery_context_json = {
                    "errorCode": readiness.error_code,
                    "missingRequiredUnitIds": list(readiness.missing_required_unit_ids),
                }
        if mode == "retry":
            if status not in {JobProfileStatus.FAILED.value, JobProfileStatus.REVIEW_REQUIRED.value}:
                raise BusinessError("job_profile_retry_requires_failed", "仅失败或待确认的岗位画像可以重试", status_code=409)
            if version.recovery_code == "job_profile_ready_retryable":
                source_run = self.db.get(WorkflowRun, version.profile_workflow_run_id) if version.profile_workflow_run_id else None
                if source_run is None:
                    raise BusinessError("job_profile_ready_retry_run_missing", "原岗位画像任务已不可用，请重新生成画像", status_code=409)
                profile_revision = int((source_run.input_json or {}).get("profileRevision") or 1)
                profile = self.db.scalar(select(JobRequirementProfileRecord).where(
                    JobRequirementProfileRecord.jd_version_id == version.jd_version_id,
                    JobRequirementProfileRecord.revision == profile_revision,
                ))
                if profile is None:
                    raise BusinessError("job_profile_ready_retry_artifact_missing", "已生成的岗位画像不可用，请重新生成", status_code=409)
                run, _ = WorkflowQueue(self.db).enqueue(
                    workflow_type=WORKFLOW_TYPE,
                    subject_type="job_version",
                    subject_id=version.jd_version_id,
                    triggered_by=triggered_by,
                    input_json={
                        **self._queue_input_for_version(version, profile_revision=profile_revision),
                        "retryMode": "mark_ready",
                        "existingJobProfileId": profile.job_profile_id,
                    },
                    reuse_active=True,
                )
                self.db.flush()
                version.profile_workflow_run_id = run.workflow_run_id
                JobProcessService.mark_profile(version, status=JobProfileStatus.QUEUED, now=_now())
                return run
        elif mode == "regenerate":
            # 历史空画像仍有 active 指针，正需要通过“重新生成”完成自助修复。
            # 因此这里检查画像记录是否存在；是否可进入 V1 由统一 readiness 校验决定。
            active_profile = (
                self.db.get(
                    JobRequirementProfileRecord, version.active_job_profile_id
                )
                if version.active_job_profile_id
                else None
            )
            if active_profile is None:
                raise BusinessError(
                    "job_profile_regenerate_requires_existing",
                    "岗位尚无可重新生成的画像，请使用生成岗位画像操作",
                    status_code=409,
                )
        else:
            raise BusinessError("job_profile_run_mode_invalid", "不支持的岗位画像操作", status_code=422)

        run = self.enqueue_for_version(version, triggered_by=triggered_by, force=True)
        if run is None:
            raise RuntimeError("job_profile_run_not_created")
        return run

    def load_ready_retry_profile(self, workflow_run_id: str) -> dict[str, Any] | None:
        """返回本次就绪发布重试绑定的画像；普通编译任务返回 None。"""
        run = self.db.get(WorkflowRun, workflow_run_id)
        if run is None or str((run.input_json or {}).get("retryMode") or "") != "mark_ready":
            return None
        profile_id = str((run.input_json or {}).get("existingJobProfileId") or "")
        profile = self.db.get(JobRequirementProfileRecord, profile_id) if profile_id else None
        if profile is None:
            raise RuntimeError("job_profile_ready_retry_artifact_missing")
        frozen = self.load_input_for_run(workflow_run_id)
        return {
            **dict(profile.profile_json or {}),
            "job_id": frozen.job_id,
            "source_sha256": frozen.source_sha256,
            "preset_model_id": frozen.preset_model_id,
            "preset_model_version": frozen.preset_model_version,
            "schema_version": profile.profile_schema_version,
            "versions": {"algorithm_process_version": profile.algorithm_version},
            "job_profile_version_id": profile.job_profile_id,
        }
    def load_input_for_run(self, workflow_run_id: str) -> JobProfileCompilationInput:
        """读取并验证 workflow 输入与 JD 版本是否仍为同一份冻结事实。"""
        run = self.db.get(WorkflowRun, workflow_run_id)
        if run is None or run.workflow_type != WORKFLOW_TYPE:
            raise RuntimeError("job_profile_workflow_run_missing")
        if run.subject_type != "job_version" or not run.subject_id:
            raise RuntimeError("job_profile_workflow_subject_invalid")
        version = self.db.get(JobVersionRecord, run.subject_id)
        if version is None:
            raise RuntimeError("job_version_missing")
        job = self.db.get(Job, version.job_id)
        if job is None or job.deleted_at is not None:
            raise RuntimeError("job_missing")

        frozen = self._input_from_version(
            version,
            profile_revision=int((run.input_json or {}).get("profileRevision") or 1),
        )
        for key, value in frozen.checkpoint_input().items():
            if (run.input_json or {}).get(key) != value:
                raise RuntimeError(f"job_profile_workflow_input_mismatch:{key}")
        return frozen

    def mark_processing(self, workflow_run_id: str) -> dict[str, Any]:
        """第一步发布运行状态；不执行外部调用。"""
        frozen = self.load_input_for_run(workflow_run_id)
        version = self.db.get(JobVersionRecord, frozen.jd_version_id)
        if version is None:
            raise RuntimeError("job_version_missing")
        JobProcessService.mark_profile(version, status=JobProfileStatus.PROCESSING, now=_now())
        version.recovery_code = None
        version.recovery_context_json = {}
        return frozen.checkpoint_input()

    @staticmethod
    def compile(frozen: JobProfileCompilationInput) -> dict[str, Any]:
        """调用算法包生成候选人无关的岗位画像。

        输入：冻结 JD 文本、内容哈希、预设能力模型版本。
        输出：JDUnit、JDCapability、资格约束、算法版本和退化标记；不写数据库。
        """
        preset_model = get_preset_model(
            frozen.preset_model_id,
            frozen.preset_model_version,
        )
        profile = compile_job_profile(
            frozen.job_id,
            frozen.source_text,
            frozen_job_json=frozen.frozen_job_json,
            preset_model=preset_model,
        )
        if profile.get("job_id") != frozen.job_id:
            raise RuntimeError("compiled_job_profile_job_mismatch")
        if profile.get("source_sha256") != frozen.source_sha256:
            raise RuntimeError("compiled_job_profile_source_mismatch")
        if profile.get("preset_model_id") != frozen.preset_model_id:
            raise RuntimeError("compiled_job_profile_model_mismatch")
        if profile.get("preset_model_version") != frozen.preset_model_version:
            raise RuntimeError("compiled_job_profile_model_version_mismatch")
        if str(dict(profile.get("versions") or {}).get("algorithm_process_version") or "") != frozen.algorithm_version:
            raise RuntimeError("compiled_job_profile_algorithm_version_mismatch")
        return profile

    def publish(self, workflow_run_id: str, profile: dict[str, Any]) -> dict[str, Any]:
        """原子持久化一个不可变画像 revision，但尚不切换 active 指针。"""
        frozen = self.load_input_for_run(workflow_run_id)
        # 仅持久化正式岗位能力事实；身份、输入哈希和调用轨迹分别由具名列或 Artifact 承担。
        profile_json = {
            key: profile.get(key)
            for key in (
                "jd_units", "assessment_units", "job_capabilities",
                "qualification_constraints", "hard_screening_suggestions",
                "requirement_classification_degraded", "degraded",
            )
        }
        readiness = evaluate_job_profile_json(profile_json)
        if not readiness.ready:
            detail = ",".join(readiness.missing_required_unit_ids)
            raise RuntimeError(
                f"job_profile_not_screening_ready:{readiness.error_code}:{detail}"
            )
        profile_schema_version = str(profile.get("schema_version") or "job_requirement_profile_v1")
        algorithm_version = str(
            dict(profile.get("versions") or {}).get("algorithm_process_version")
            or frozen.algorithm_version
        )
        existing = self.db.scalar(
            select(JobRequirementProfileRecord).where(
                JobRequirementProfileRecord.jd_version_id == frozen.jd_version_id,
                JobRequirementProfileRecord.revision == frozen.profile_revision,
            )
        )
        if existing is None:
            base_id = str(profile.get("job_profile_version_id") or "")
            profile_id = (
                base_id
                if frozen.profile_revision == 1 and base_id
                else f"JPR_{frozen.jd_version_id[-16:]}_{frozen.profile_revision}"
            )
            existing = JobRequirementProfileRecord(
                job_profile_id=profile_id[:96],
                job_id=frozen.job_id,
                jd_version_id=frozen.jd_version_id,
                version=frozen.jd_version,
                revision=frozen.profile_revision,
                source_sha256=frozen.source_sha256,
                profile_schema_version=profile_schema_version,
                algorithm_version=algorithm_version,
                profile_json=profile_json,
            )
            self.db.add(existing)
            self.db.flush()
        elif existing.source_sha256 != frozen.source_sha256:
            raise RuntimeError("existing_job_profile_source_mismatch")
        policy = self._publish_generated_hard_screening_draft(
            frozen=frozen,
            workflow_run_id=workflow_run_id,
            rules=list(profile_json.get("hard_screening_suggestions") or []),
            degraded=bool(profile_json.get("requirement_classification_degraded")),
        )
        return {
            **frozen.checkpoint_input(),
            "jobProfileId": existing.job_profile_id,
            "hardScreeningPolicyId": policy.policy_id if policy else None,
        }

    def _publish_generated_hard_screening_draft(
        self,
        *,
        frozen: JobProfileCompilationInput,
        workflow_run_id: str,
        rules: list[dict[str, Any]],
        degraded: bool,
    ) -> HardScreeningPolicy | None:
        """Create one disabled draft per frozen JD without replacing user-owned policy."""
        latest = self.db.scalar(
            select(HardScreeningPolicy)
            .where(HardScreeningPolicy.job_id == frozen.job_id)
            .order_by(HardScreeningPolicy.version.desc())
        )
        latest_meta = dict(latest.rules_json or {}) if latest is not None else {}
        latest_source = str(latest_meta.get("source_jd_version_id") or "")
        if latest_source == frozen.jd_version_id:
            return latest
        if latest is not None and not latest_source:
            # Historical/user-managed policies have no source version. Do not silently
            # supersede them during a profile regeneration.
            return latest

        run = self.db.get(WorkflowRun, workflow_run_id)
        if run is None or not run.triggered_by:
            raise RuntimeError("job_profile_policy_actor_missing")
        version = int(
            self.db.scalar(
                select(func.max(HardScreeningPolicy.version)).where(
                    HardScreeningPolicy.job_id == frozen.job_id
                )
            )
            or 0
        ) + 1
        policy = HardScreeningPolicy(
            policy_id=f"HSP_{uuid4().hex[:20].upper()}",
            job_id=frozen.job_id,
            version=version,
            enabled=False,
            rules_json={
                "items": rules,
                "schema_version": "hard_screening_policy_v1",
                "source_jd_version_id": frozen.jd_version_id,
                "generation_mode": "job_requirement_auto",
                "generation_status": "degraded" if degraded else "pending_confirmation",
            },
            created_by=run.triggered_by,
            created_at=_now(),
        )
        self.db.add(policy)
        self.db.flush()
        return policy

    def mark_ready(self, workflow_run_id: str) -> dict[str, Any]:
        """只在本次 revision 已持久化后切换当前有效画像，并唤醒等待申请。"""
        frozen = self.load_input_for_run(workflow_run_id)
        version = self.db.get(JobVersionRecord, frozen.jd_version_id)
        profile = self.db.scalar(
            select(JobRequirementProfileRecord).where(
                JobRequirementProfileRecord.jd_version_id == frozen.jd_version_id,
                JobRequirementProfileRecord.revision == frozen.profile_revision,
            )
        )
        if version is None or profile is None:
            raise RuntimeError("job_profile_missing_before_ready")
        readiness = evaluate_job_profile_record(
            profile,
            job_id=frozen.job_id,
            jd_version_id=frozen.jd_version_id,
        )
        if not readiness.ready:
            detail = ",".join(readiness.missing_required_unit_ids)
            raise RuntimeError(
                f"job_profile_not_screening_ready_before_ready:{readiness.error_code}:{detail}"
            )
        version.active_job_profile_id = profile.job_profile_id
        version.profile_workflow_run_id = workflow_run_id
        JobProcessService.mark_profile(version, status=JobProfileStatus.READY, now=_now())
        version.recovery_code = None
        version.recovery_context_json = {}

        event = build_job_profile_ready_event(
            event_id=f"job-profile-ready:{workflow_run_id}",
            job_id=frozen.job_id,
            jd_version_id=frozen.jd_version_id,
            job_profile_id=profile.job_profile_id,
            occurred_at=version.profile_completed_at or _now(),
        )
        run = self.db.get(WorkflowRun, workflow_run_id)
        released_application_ids = release_applications_waiting_for_job_profile(
            self.db,
            job_id=event.job_id,
            jd_version_id=event.jd_version_id,
            job_profile_id=event.job_profile_id,
            actor_id=str(run.triggered_by) if run is not None else "",
            source_key=event.event_id,
        )
        return {
            **frozen.checkpoint_input(),
            "jobProfileId": profile.job_profile_id,
            "jobProfileReadyEventId": event.event_id,
            "releasedApplicationIds": released_application_ids,
        }

    def _profile_for_version(
        self, version: JobVersionRecord | None
    ) -> JobRequirementProfileRecord | None:
        """返回该 JD 版本当前对新申请有效的画像，而不是任意历史 revision。"""
        if version is None or not version.active_job_profile_id:
            return None
        profile = self.db.get(JobRequirementProfileRecord, version.active_job_profile_id)
        readiness = evaluate_job_profile_record(
            profile,
            job_id=version.job_id,
            jd_version_id=version.jd_version_id,
        )
        return profile if readiness.ready else None

    def _next_profile_revision(self, version: JobVersionRecord) -> int:
        latest = self.db.scalar(
            select(JobRequirementProfileRecord.revision)
            .where(JobRequirementProfileRecord.jd_version_id == version.jd_version_id)
            .order_by(JobRequirementProfileRecord.revision.desc())
            .limit(1)
        )
        return int(latest or 0) + 1

    @staticmethod
    def _queue_input_for_version(
        version: JobVersionRecord,
        *,
        profile_revision: int,
    ) -> dict[str, Any]:
        return {
            "schemaVersion": INPUT_SCHEMA_VERSION,
            "jobId": version.job_id,
            "jdVersionId": version.jd_version_id,
            "jdVersion": version.version,
            "profileRevision": profile_revision,
            "sourceSha256": version.source_sha256,
            "presetModelId": version.preset_model_id,
            "presetModelVersion": version.preset_model_version,
            "algorithmVersion": version.job_capability_algorithm_version,
        }

    @classmethod
    def _input_from_version(
        cls,
        version: JobVersionRecord,
        *,
        profile_revision: int,
    ) -> JobProfileCompilationInput:
        queue_input = cls._queue_input_for_version(
            version,
            profile_revision=profile_revision,
        )
        return JobProfileCompilationInput(
            job_id=version.job_id,
            jd_version_id=version.jd_version_id,
            jd_version=version.version,
            profile_revision=profile_revision,
            source_sha256=version.source_sha256,
            source_text=version.source_text,
            preset_model_id=str(queue_input["presetModelId"]),
            preset_model_version=str(queue_input["presetModelVersion"]),
            algorithm_version=str(queue_input["algorithmVersion"]),
            frozen_job_json=dict(version.frozen_job_json or {}),
        )
