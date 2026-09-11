from __future__ import annotations

import html
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.infrastructure.workflow_runtime import WorkflowQueue
from backend.app.shared.audit import record_audit_event
from backend.app.modules.auth.public import AuthorizationService
from backend.app.models.entities import (
    Application,
    Candidate,
    CandidateProfile,
    HardScreeningPolicy,
    HardScreeningResult,
    HardScreeningCriterion,
    HumanDecision,
    Job,
    JobVersionRecord,
    StageHistory,
    ResumeProfileRecord,
    ResumeSubmission,
    User,
    WorkflowRun,
    WorkflowStepCheckpoint,
)
from backend.app.modules.applications.public import ApplicationProcessService
from backend.app.modules.applications.application_recovery import (
    ApplicationRecoveryCode,
    clear_application_recovery,
    set_application_recovery,
)
from backend.app.shared.errors import BusinessError
from backend.app.modules.assessment.hard_screening.hard_screening_catalog_service import (
    BINDING_SPECS,
    HardScreeningCatalogService,
)
from backend.app.modules.assessment.commands.scoring_request_commands import ScoringRequestCommands


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20].upper()}"


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _display_hard_screening_value(value: Any) -> str:
    def number_text(item: Any) -> str:
        try:
            number = float(item)
        except (TypeError, ValueError):
            return str(item or "").strip()
        return str(int(number)) if number.is_integer() else str(number)

    if isinstance(value, list):
        return "、".join(_public_hard_screening_text(item) for item in value if str(item).strip())
    if isinstance(value, dict):
        minimum, maximum = value.get("min"), value.get("max")
        if minimum is not None and maximum is not None:
            left, right = number_text(minimum), number_text(maximum)
            return left if left == right else f"{left} 至 {right}"
        if minimum is not None:
            return f"{number_text(minimum)}及以上"
        if maximum is not None:
            return f"{number_text(maximum)}及以下"
        return ""
    if isinstance(value, (int, float)):
        return number_text(value)
    return _public_hard_screening_text(value)


def _public_hard_screening_text(value: Any) -> str:
    """只在展示边界还原 HTML 实体；冻结事实和审计数据保持原样。"""

    text = str(value or "").strip()
    # 兼容被上游重复转义成 &amp;#x...; 的值，同时限制次数避免无界处理。
    for _ in range(2):
        decoded = html.unescape(text)
        if decoded == text:
            break
        text = decoded
    return text


def _hard_screening_requirement_text(rule: dict[str, Any]) -> str:
    """把冻结的机器规则转换成稳定的用户文案，避免浏览器解释运算符。"""

    expected = _display_hard_screening_value(rule.get("expected_value"))
    operator = str(rule.get("operator") or "")
    if operator == "exists":
        return expected or "必须具备"
    if operator == "degree_at_least":
        return expected if expected.endswith("及以上") else f"{expected}及以上"
    if operator == "education_status_is":
        return f"最高学历状态为：{expected}" if expected else "需核验最高学历状态"
    if operator == "year_between":
        return f"最高学历毕业年份：{expected}" if expected else "需核验最高学历毕业年份"
    if operator == "years_at_least":
        value = expected[:-3] if expected.endswith("及以上") else expected
        return f"{value}及以上" if "年" in value else f"{value} 年及以上"
    if operator == "years_between":
        return expected if "年" in expected else f"{expected} 年"
    if operator == "contains_any":
        return f"满足任一项：{expected}"
    if operator == "contains_all":
        return f"需全部满足：{expected}"
    if operator == "not_contains_any":
        return f"不得包含：{expected}"
    return expected or str(rule.get("description") or "").strip() or "需人工核验"


def _hard_screening_result_rule(rule: dict[str, Any]) -> dict[str, Any]:
    reason_code = str(rule.get("reason_code") or "")
    reason = _public_hard_screening_text(rule.get("reason"))
    if reason_code == "model_result_invalid":
        reason = "系统未能完成可靠的自动判断，请核对简历是否明确满足该要求后选择人工通过或不通过。"
    return {
        **rule,
        "name": _public_hard_screening_text(rule.get("name")) or "硬筛条件",
        "requirementText": _hard_screening_requirement_text(rule),
        "reason": reason,
        "source_quotes": [
            _public_hard_screening_text(quote)
            for quote in list(rule.get("source_quotes") or [])
            if _public_hard_screening_text(quote)
        ],
    }


class HardScreeningService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.queue = WorkflowQueue(db)

    def latest_policy(
        self,
        job_id: str,
        *,
        enabled_only: bool = False,
        jd_version_id: str | None = None,
    ) -> HardScreeningPolicy | None:
        query = select(HardScreeningPolicy).where(HardScreeningPolicy.job_id == job_id)
        policies = list(
            self.db.scalars(query.order_by(HardScreeningPolicy.version.desc()))
        )
        policy = next(
            (
                item
                for item in policies
                if not jd_version_id
                or not str((item.rules_json or {}).get("source_jd_version_id") or "")
                or str((item.rules_json or {}).get("source_jd_version_id")) == jd_version_id
            ),
            None,
        )
        if enabled_only and (policy is None or not policy.enabled):
            return None
        return policy

    def get_policy_view(self, job_id: str) -> dict[str, Any]:
        if self.db.get(Job, job_id) is None:
            raise BusinessError("job_not_found", "未找到岗位", status_code=404)
        policy = self.latest_policy(job_id)
        if policy is None:
            return {
                "jobId": job_id,
                "policyId": None,
                "version": 0,
                "enabled": False,
                "rules": [],
                "generationMode": None,
                "generationStatus": None,
                "sourceJdVersionId": None,
            }
        return self._policy_json(policy)

    def save_policy(
        self,
        user: User,
        job_id: str,
        *,
        enabled: bool,
        rules: list[dict[str, Any]],
    ) -> HardScreeningPolicy:
        job = self.db.get(Job, job_id)
        if job is None:
            raise BusinessError("job_not_found", "未找到岗位", status_code=404)
        AuthorizationService(self.db).require_business_action(
            user, "hard_screening.policy.manage", department_id=job.department_id
        )
        latest = self.latest_policy(job_id)
        compiled_rules = [self._compile_rule(rule) for rule in rules]
        comparable_rules = [
            {key: value for key, value in rule.items() if key != "rule_id"}
            for rule in compiled_rules
        ]
        latest_rules = (
            [
                {key: value for key, value in rule.items() if key != "rule_id"}
                for rule in list((latest.rules_json or {}).get("items") or [])
            ]
            if latest is not None
            else []
        )
        if latest is not None and latest.enabled == enabled and latest_rules == comparable_rules:
            return latest
        version = int(
            self.db.scalar(
                select(func.max(HardScreeningPolicy.version)).where(
                    HardScreeningPolicy.job_id == job_id
                )
            )
            or 0
        ) + 1
        normalized_rules = [
            {
                **rule,
                "rule_id": str(rule.get("rule_id") or _id("HSRULE")),
            }
            for rule in compiled_rules
        ]
        latest_metadata = dict(latest.rules_json or {}) if latest is not None else {}
        source_jd_version_id = str(
            latest_metadata.get("source_jd_version_id") or ""
        )
        if not source_jd_version_id:
            current_version = self.db.scalar(
                select(JobVersionRecord)
                .where(JobVersionRecord.job_id == job_id)
                .order_by(JobVersionRecord.version.desc())
                .limit(1)
            )
            source_jd_version_id = (
                current_version.jd_version_id if current_version is not None else ""
            )
        generated = str(latest_metadata.get("generation_mode") or "") == "job_requirement_auto"
        policy = HardScreeningPolicy(
            policy_id=_id("HSP"),
            job_id=job_id,
            version=version,
            enabled=enabled,
            rules_json={
                "items": normalized_rules,
                "schema_version": "hard_screening_policy_v1",
                "source_jd_version_id": source_jd_version_id or None,
                "generation_mode": "user_confirmed_auto" if generated else "user_managed",
                "generation_status": "confirmed" if enabled else "disabled",
            },
            created_by=user.user_id,
            created_at=_now(),
        )
        self.db.add(policy)
        self.db.flush()
        record_audit_event(
            self.db,
            actor=user,
            action="hard_screening.policy.update",
            target_type="job",
            target_id=job_id,
            summary=f"更新岗位硬筛策略（版本 {version}）",
            details={
                "policyId": policy.policy_id,
                "enabled": enabled,
                "ruleCount": len(normalized_rules),
            },
        )
        # 策略版本、审计事件和命令幂等记录必须由同一外层事务提交。
        self.db.flush()
        return policy

    def _compile_rule(self, rule: dict[str, Any]) -> dict[str, Any]:
        criterion_id = str(rule.get("criterion_id") or rule.get("criterionId") or "").strip()
        if criterion_id:
            criterion = self.db.get(HardScreeningCriterion, criterion_id)
            if criterion is None or criterion.deleted_at is not None or not criterion.enabled:
                raise BusinessError("hard_screening_criterion_unavailable", "硬筛选项已停用或不存在")
            return self._compile_catalog_rule(criterion, rule)

        criterion_type = str(rule.get("criterion_type") or "")
        if not criterion_type:
            if not rule.get("source_scope") or not rule.get("operator"):
                raise BusinessError("hard_screening_rule_type_missing", "硬筛规则缺少筛选项类型")
            return {**rule, "schema_version": "hard_screening_rule_v1"}
        mappings = {
            "minimum_degree": ("最低学历", "education", "degree_at_least"),
            "highest_education_status": ("最高学历状态", "education", "education_status_is"),
            "highest_education_graduation_year": ("最高学历毕业年份", "education", "year_between"),
            "minimum_experience_years": ("相关工作经验", "work_experience", "years_at_least"),
            "project_experience": ("相关项目经验", "project_experience", "semantic_match"),
            "required_skill": ("必备技能", "skills", "semantic_match"),
            "certification": ("证书/资质", "certifications", "semantic_match"),
            "custom": ("自定义条件", "full_resume", "semantic_match"),
        }
        mapped = mappings.get(criterion_type)
        if mapped is None:
            raise BusinessError("hard_screening_rule_type_unsupported", "不支持的硬筛筛选项")
        name, source_scope, operator = mapped
        expected = rule.get("expected_value")
        if expected is None or (isinstance(expected, str) and not expected.strip()) or (isinstance(expected, list) and not any(str(item).strip() for item in expected)):
            raise BusinessError("hard_screening_rule_value_missing", f"{name}未填写要求")
        if criterion_type == "highest_education_graduation_year":
            try:
                year = float(expected)
            except (TypeError, ValueError) as exc:
                raise BusinessError(
                    "hard_screening_graduation_year_invalid",
                    "最高学历毕业年份必须是数字",
                ) from exc
            expected = {"min": year, "max": year}
        return {**rule, "criterion_type": criterion_type, "name": name, "source_scope": source_scope, "operator": operator, "expected_value": expected, "description": str(expected).strip() if criterion_type == "custom" else str(rule.get("description") or ""), "schema_version": "hard_screening_rule_v1"}

    @staticmethod
    def _compile_catalog_rule(criterion: HardScreeningCriterion, rule: dict[str, Any]) -> dict[str, Any]:
        condition = dict(rule.get("condition") or {})
        allowed = list((criterion.allowed_values_json or {}).get("items") or [])
        binding = criterion.evaluation_binding
        binding_spec = BINDING_SPECS.get(binding)
        if binding_spec is None or binding_spec[0] != criterion.value_mode:
            raise BusinessError("hard_screening_binding_invalid", "硬筛项的取值方式与核验来源不匹配")
        _, source_scope, operator = binding_spec
        if criterion.value_mode == "select":
            selected = [str(item).strip() for item in list(condition.get("selected_values") or condition.get("selectedValues") or []) if str(item).strip()]
            if len(selected) != 1 or selected[0] not in allowed:
                raise BusinessError("hard_screening_select_value_invalid", f"{criterion.name}必须选择一个有效取值")
            expected, normalized = selected[0], {"selected_values": selected}
        elif criterion.value_mode == "number":
            minimum, maximum = condition.get("min"), condition.get("max")
            if minimum is None and maximum is None:
                raise BusinessError("hard_screening_number_value_missing", f"{criterion.name}至少填写最小值或最大值")
            try:
                minimum = float(minimum) if minimum is not None else None
                maximum = float(maximum) if maximum is not None else None
            except (TypeError, ValueError) as exc:
                raise BusinessError("hard_screening_number_value_invalid", f"{criterion.name}必须是数字") from exc
            if minimum is not None and maximum is not None and minimum > maximum:
                raise BusinessError("hard_screening_number_range_invalid", f"{criterion.name}的最小值不能大于最大值")
            expected, normalized = {"min": minimum, "max": maximum}, {"min": minimum, "max": maximum}
        else:
            value = str(condition.get("text") or "").strip()
            if not value:
                raise BusinessError("hard_screening_text_value_missing", f"{criterion.name}未填写要求")
            expected, normalized = value, {"text": value}
        return {"rule_id": str(rule.get("rule_id") or rule.get("ruleId") or _id("HSRULE")), "criterion_id": criterion.criterion_id, "criterion_type": criterion.code, "name": criterion.name, "value_mode": criterion.value_mode, "evaluation_binding": binding, "condition": normalized, "source_scope": source_scope, "operator": operator, "expected_value": expected, "description": "", "enabled": bool(rule.get("enabled", True)), "schema_version": "hard_screening_rule_v2"}
    def enqueue_for_application(
        self,
        app: Application,
        user: User,
        policy: HardScreeningPolicy,
    ) -> WorkflowRun:
        if policy.job_id != app.job_id:
            raise BusinessError(
                "hard_screening_policy_job_mismatch",
                "硬筛策略不属于该岗位申请。",
                status_code=409,
            )
        existing = self.queue.active_run(
            workflow_type="hard_screening_workflow",
            subject_type="application",
            subject_id=app.application_id,
        )
        if existing is not None:
            return existing
        if app.status != "hard_screening_pending":
            raise BusinessError(
                "hard_screening_enqueue_state_invalid",
                f"当前申请状态 {app.status} 不能进入硬筛队列",
                status_code=409,
            )
        app.current_owner = "系统"
        app.updated_at = _now()
        app.hard_screening_policy_id = policy.policy_id
        app.hard_screening_status = "pending"
        clear_application_recovery(app)
        run, _ = self.queue.enqueue(
            workflow_type="hard_screening_workflow",
            subject_type="application",
            subject_id=app.application_id,
            application_id=app.application_id,
            triggered_by=user.user_id,
            input_json={"policy_id": policy.policy_id},
            reuse_active=False,
        )
        self._stage(
            app,
            user,
            "enqueue_hard_screening",
            "hard_screening_pending",
            "hard_screening_pending",
            "已创建硬筛后台任务。",
        )
        return run

    def retry_for_application(self, app: Application, user: User) -> WorkflowRun:
        """Reset only the hard-screen boundary and enqueue a fresh hard-screen run."""
        AuthorizationService(self.db).require_application_action(
            user, app, "retry_hard_screening"
        )
        if app.status != "hard_screening_review":
            raise BusinessError(
                "hard_screening_retry_state_invalid",
                "只有等待人工复核的硬筛任务可以重新运行",
                status_code=409,
            )
        retryable_codes = {
            ApplicationRecoveryCode.HARD_SCREENING_RETRYABLE.value,
            ApplicationRecoveryCode.HARD_SCREENING_PUBLISH_RETRYABLE.value,
            ApplicationRecoveryCode.HARD_SCREENING_SOURCE_REVIEW_REQUIRED.value,
            ApplicationRecoveryCode.HARD_SCREENING_POLICY_REQUIRED.value,
        }
        if str(app.recovery_code or "") not in retryable_codes:
            raise BusinessError(
                "hard_screening_retry_input_unchanged",
                "当前问题无法通过重复运行硬筛解决，请先按页面提示处理来源或人工核对。",
                status_code=409,
            )
        policy_id = str(app.hard_screening_policy_id or "")
        policy = self.db.get(HardScreeningPolicy, policy_id) if policy_id else None
        if policy is None and str(app.recovery_code or "") == ApplicationRecoveryCode.HARD_SCREENING_POLICY_REQUIRED.value:
            # 用户先在岗位管理保存策略；重试时绑定该岗位最新启用的策略。
            policy = self.db.scalar(
                select(HardScreeningPolicy)
                .where(
                    HardScreeningPolicy.job_id == app.job_id,
                    HardScreeningPolicy.enabled.is_(True),
                )
                .order_by(HardScreeningPolicy.version.desc())
            )
            if policy is not None:
                app.hard_screening_policy_id = policy.policy_id
        if policy is None:
            raise BusinessError(
                "hard_screening_retry_policy_missing",
                "原硬筛策略已不存在，请先在岗位管理中配置硬筛条件",
                status_code=409,
            )
        transition = ApplicationProcessService.plan_transition(
            app, action="retry_hard_screening"
        )
        now = _now()
        ApplicationProcessService.apply_transition(app, transition, now=now, owner="系统")
        app.hard_screening_status = "pending"
        app.hard_screening_summary = "硬筛重试任务已受理。"
        app.rejection_stage = None
        publish_retry = self._enqueue_publish_only_retry(app=app, user=user)
        if publish_retry is not None:
            self._stage(
                app,
                user,
                "enqueue_hard_screening",
                "hard_screening_pending",
                "hard_screening_pending",
                "硬筛结果工件已复用，正在重新发布。",
            )
            return publish_retry
        return self.enqueue_for_application(app, user, policy)

    def _enqueue_publish_only_retry(
        self, *, app: Application, user: User,
    ) -> WorkflowRun | None:
        """仅发布失败时复制前两步不可变检查点，不重复调用硬筛 LLM。"""

        original = self.db.scalar(
            select(WorkflowRun)
            .where(
                WorkflowRun.application_id == app.application_id,
                WorkflowRun.workflow_type == "hard_screening_workflow",
                WorkflowRun.status == "failed",
            )
            .order_by(WorkflowRun.updated_at.desc(), WorkflowRun.workflow_run_id.desc())
        )
        if original is None:
            return None
        failed = self.db.scalar(
            select(WorkflowStepCheckpoint)
            .where(
                WorkflowStepCheckpoint.workflow_run_id == original.workflow_run_id,
                WorkflowStepCheckpoint.status == "failed",
            )
            .order_by(WorkflowStepCheckpoint.step_order.desc())
        )
        if failed is None or failed.step_name != "publish_hard_screening":
            return None
        retry = self.queue.retry(
            original,
            # Checkpoint 输入哈希包含 triggered_by。保留原执行身份才能安全复用；
            # 本次操作用户仍由硬筛重试命令的审计记录保存。
            triggered_by=original.triggered_by,
            reason="retry_publish_hard_screening",
            definition_version=1,
        )
        retry.input_json = {
            **dict(retry.input_json or {}),
            "retry_scope": "publish_hard_screening",
        }
        succeeded = self.db.scalars(
            select(WorkflowStepCheckpoint)
            .where(
                WorkflowStepCheckpoint.workflow_run_id == original.workflow_run_id,
                WorkflowStepCheckpoint.status == "succeeded",
                WorkflowStepCheckpoint.step_order < failed.step_order,
            )
            .order_by(WorkflowStepCheckpoint.step_order)
        ).all()
        for checkpoint in succeeded:
            self.db.add(
                WorkflowStepCheckpoint(
                    checkpoint_id=_id("WSC"),
                    workflow_run_id=retry.workflow_run_id,
                    step_name=checkpoint.step_name,
                    step_order=checkpoint.step_order,
                    definition_version=checkpoint.definition_version,
                    status="succeeded",
                    input_hash=checkpoint.input_hash,
                    idempotency_key=f"{retry.workflow_run_id}:{checkpoint.step_name}"[:128],
                    external_request_id=(
                        f"{retry.workflow_run_id}:{checkpoint.step_name}:publish-retry"
                    )[:128],
                    output_refs_json=dict(checkpoint.output_refs_json or {}),
                    attempt_count=checkpoint.attempt_count,
                    max_attempts_snapshot=checkpoint.max_attempts_snapshot,
                    max_poll_attempts_snapshot=checkpoint.max_poll_attempts_snapshot,
                    poll_count=checkpoint.poll_count,
                    completed_at=_now(),
                    created_at=_now(),
                    updated_at=_now(),
                )
            )
        clear_application_recovery(app)
        return retry

    def build_workflow_input(self, run_id: str, worker_id: str) -> dict[str, Any]:
        run = self.db.get(WorkflowRun, run_id)
        if run is None or run.status != "running" or run.lease_owner != worker_id:
            raise RuntimeError("workflow_lease_lost")
        app = self.db.get(Application, run.application_id)
        if app is None or app.status not in {"hard_screening_pending", "hard_screening_running"}:
            raise RuntimeError("application_not_ready_for_hard_screening")
        policy_id = str((run.input_json or {}).get("policy_id") or "")
        policy = self.db.get(HardScreeningPolicy, policy_id)
        candidate = self.db.get(Candidate, app.candidate_id)
        if policy is None:
            raise RuntimeError("hard_screening_policy_missing")
        if candidate is None:
            raise RuntimeError("hard_screening_candidate_missing")
        if policy.job_id != app.job_id:
            raise RuntimeError("hard_screening_policy_job_mismatch")
        resume_submission = (
            self.db.get(ResumeSubmission, app.adopted_resume_submission_id)
            if app.adopted_resume_submission_id else None
        )
        if resume_submission is None:
            raise RuntimeError("application_adopted_resume_submission_missing")
        resume_text = str(resume_submission.parsed_text or "").strip()
        # 空原文不是“候选人不满足条件”的证据。必须在冻结阶段转为来源修复，
        # 避免 contains/exists 等确定性规则把解析异常误判为硬筛淘汰。
        if not resume_text:
            raise RuntimeError("hard_screening_resume_text_missing")
        resume_profile = (
            self.db.get(ResumeProfileRecord, resume_submission.output_resume_profile_id)
            if resume_submission.output_resume_profile_id
            else None
        )
        if resume_profile is None:
            raise RuntimeError("hard_screening_resume_profile_missing")
        frozen_candidate_facts = dict((resume_profile.profile_data or {}).get("candidate_facts") or {}) if resume_profile is not None else {}
        return {
            "application_id": app.application_id,
            "resume_text": resume_text,
            "resume_profile": dict(resume_profile.profile_data or {}),
            "candidate_facts": frozen_candidate_facts,
            "rules": list((policy.rules_json or {}).get("items") or []),
            "llm_config": (
                dict((run.input_json or {}).get("llm_config"))
                if isinstance((run.input_json or {}).get("llm_config"), dict)
                else {}
            ),
            "policy_id": policy.policy_id,
            "triggered_by": run.triggered_by,
        }

    def mark_workflow_running(self, run: WorkflowRun) -> None:
        """在冻结输入已持久化的短事务中推进 Application 到运行中。"""
        app = self.db.get(Application, run.application_id)
        if app is None:
            raise RuntimeError("hard_screening_application_missing")
        if app.status == "hard_screening_running":
            return
        now = _now()
        transition = ApplicationProcessService.plan_transition(app, action="run_hard_screening")
        app.hard_screening_status = "running"
        clear_application_recovery(app)
        self.db.add(
            StageHistory(
                stage_history_id=_id("SH"),
                application_id=app.application_id,
                actor_role="system",
                actor_name="系统",
                action=transition.action,
                from_status=transition.from_status,
                to_status=transition.to_status,
                note="硬筛输入已冻结，开始执行。",
                created_at=now,
            )
        )
        ApplicationProcessService.apply_transition(app, transition, now=now, owner="系统")
    def complete_workflow(
        self,
        *,
        run: WorkflowRun,
        result: dict[str, Any],
    ) -> None:
        app = self.db.get(Application, run.application_id)
        if app is None or app.status != "hard_screening_running":
            raise RuntimeError("application_status_changed")
        policy_id = str((run.input_json or {}).get("policy_id") or "")
        row = self.db.scalar(
            select(HardScreeningResult).where(
                HardScreeningResult.application_id == app.application_id,
                HardScreeningResult.policy_id == policy_id,
            )
        )
        now = _now()
        if row is None:
            row = HardScreeningResult(
                result_id=_id("HSR"),
                application_id=app.application_id,
                policy_id=policy_id,
                workflow_run_id=run.workflow_run_id,
                status=str(result["status"]),
                summary=str(result.get("summary") or ""),
                rule_results_json=list(result.get("rule_results") or []),
                created_at=now,
                updated_at=now,
            )
            self.db.add(row)
        else:
            row.workflow_run_id = run.workflow_run_id
            row.status = str(result["status"])
            row.summary = str(result.get("summary") or "")
            row.rule_results_json = list(result.get("rule_results") or [])
            row.updated_at = now

        result_status = str(result["status"])
        if result_status == "passed":
            next_action = "complete_hard_screening_passed"
        elif result_status == "failed":
            next_action = "complete_hard_screening_failed"
        else:
            next_action = "complete_hard_screening_review"
        transition = ApplicationProcessService.plan_transition(app, action=next_action)
        app.hard_screening_status = result_status
        app.hard_screening_summary = str(result.get("summary") or "")
        app.rejection_stage = "hard_screening" if result_status == "failed" else None
        if result_status == "manual_review":
            manual_reason_codes = {
                str(item.get("reason_code") or "")
                for item in list(result.get("rule_results") or [])
                if isinstance(item, dict)
                and str(item.get("status") or "") == "manual_review"
            }
            if "source_fact_missing" in manual_reason_codes:
                recovery_code = (
                    ApplicationRecoveryCode.HARD_SCREENING_RESUME_SOURCE_REQUIRED
                )
            elif "policy_invalid" in manual_reason_codes:
                recovery_code = ApplicationRecoveryCode.HARD_SCREENING_POLICY_REQUIRED
            elif manual_reason_codes & {
                "model_temporarily_unavailable",
                "model_result_invalid",
            }:
                recovery_code = ApplicationRecoveryCode.HARD_SCREENING_RETRYABLE
            else:
                recovery_code = ApplicationRecoveryCode.HARD_SCREENING_REVIEW_REQUIRED
            set_application_recovery(
                app,
                recovery_code,
                context={
                    "workflowRunId": run.workflow_run_id,
                    "resultStatus": result_status,
                    "reasonCodes": sorted(code for code in manual_reason_codes if code),
                },
            )
        else:
            clear_application_recovery(app)
        self.db.add(
            StageHistory(
                stage_history_id=_id("SH"),
                application_id=app.application_id,
                actor_role="system",
                actor_name="系统",
                action=transition.action,
                from_status=transition.from_status,
                to_status=transition.to_status,
                note=str(result.get("summary") or ""),
                created_at=now,
            )
        )
        ApplicationProcessService.apply_transition(app, transition, now=now, owner="HR")
        if result_status == "passed":
            self.db.flush()
            user = self.db.get(User, run.triggered_by)
            if user is None:
                raise RuntimeError("hard_screening_trigger_user_not_found")
            # 硬筛正式结果、申请状态、评分任务和当前 Step 检查点必须由 StepRunner
            # 在同一短事务提交；不得调用会自行 commit 的公开 HTTP 命令。
            ScoringRequestCommands(self.db).enqueue_in_transaction(
                app=app,
                user=user,
                input_json={"hard_screening_policy_id": policy_id},
                source="hard_screening_passed",
                allow_resume_restructuring=False,
            )

    def record_processing_failure(
        self,
        run: WorkflowRun,
        error: Exception,
        *,
        error_code: str = "",
        error_category: str = "",
        step_name: str = "",
        recovery_action: str = "",
        blocked: bool = False,
    ) -> None:
        """技术失败进入硬筛人工复核，不把外部故障伪装成自动淘汰。"""
        app = self.db.get(Application, run.application_id)
        if app is None or app.status not in {"hard_screening_pending", "hard_screening_running"}:
            return
        now = _now()
        transition = ApplicationProcessService.plan_transition(app, action="fail_hard_screening")
        app.hard_screening_status = "manual_review"
        app.hard_screening_summary = "硬筛处理失败，已转人工复核。"
        normalized_error = str(error_code or str(error) or "").casefold()
        if (
            "resume_submission" in normalized_error
            or "resume_profile" in normalized_error
            or "resume_text" in normalized_error
        ):
            recovery_code = ApplicationRecoveryCode.HARD_SCREENING_RESUME_SOURCE_REQUIRED
        elif "policy" in normalized_error:
            recovery_code = ApplicationRecoveryCode.HARD_SCREENING_POLICY_REQUIRED
        elif step_name == "publish_hard_screening":
            recovery_code = ApplicationRecoveryCode.HARD_SCREENING_PUBLISH_RETRYABLE
        elif step_name == "freeze_hard_screen_sources":
            recovery_code = ApplicationRecoveryCode.HARD_SCREENING_SOURCE_REVIEW_REQUIRED
        elif blocked:
            recovery_code = ApplicationRecoveryCode.HARD_SCREENING_REVIEW_REQUIRED
        else:
            recovery_code = ApplicationRecoveryCode.HARD_SCREENING_RETRYABLE
        set_application_recovery(
            app,
            recovery_code,
            context={
                "workflowRunId": run.workflow_run_id,
                "failedStep": step_name or None,
                "errorCode": error_code or None,
                "errorCategory": error_category or None,
                "runtimeRecoveryAction": recovery_action or None,
                "blocked": blocked,
            },
        )
        self.db.add(
            StageHistory(
                stage_history_id=_id("SH"),
                application_id=app.application_id,
                actor_role="system",
                actor_name="系统",
                action=transition.action,
                from_status=transition.from_status,
                to_status=transition.to_status,
                note=str(error)[:1000],
                created_at=now,
            )
        )
        ApplicationProcessService.apply_transition(app, transition, now=now, owner="HR")
    def latest_result_view(self, application_id: str) -> dict[str, Any]:
        row = self.db.scalar(
            select(HardScreeningResult)
            .where(HardScreeningResult.application_id == application_id)
            .order_by(HardScreeningResult.created_at.desc())
        )
        if row is None:
            app = self.db.get(Application, application_id)
            if app is None:
                raise BusinessError("application_not_found", "未找到候选申请", status_code=404)
            policy = (
                self.db.get(HardScreeningPolicy, app.hard_screening_policy_id)
                if app.hard_screening_policy_id
                else None
            )
            # 冻结输入失败时尚未产生 HardScreeningResult，但人工处理仍必须看到
            # 本申请实际绑定的规则，不能退回岗位当前策略或返回空列表。
            frozen_rules = [
                _hard_screening_result_rule({
                    **dict(rule),
                    "status": "manual_review",
                    "reason": "自动核验尚未形成结果，请根据该要求人工判断。",
                    "reason_code": "automatic_result_missing",
                    "source_quotes": [],
                })
                for rule in list((policy.rules_json or {}).get("items") or [])
                if isinstance(rule, dict) and bool(rule.get("enabled", True))
            ] if policy is not None else []
            return {
                "applicationId": application_id,
                "policyId": policy.policy_id if policy is not None else app.hard_screening_policy_id,
                "status": str(
                    app.hard_screening_status or "not_configured"
                ),
                "summary": str(
                    app.hard_screening_summary or ""
                ),
                "ruleResults": frozen_rules,
            }
        return {
            "applicationId": application_id,
            "resultId": row.result_id,
            "policyId": row.policy_id,
            "status": row.status,
            "summary": row.summary or "",
            "ruleResults": [
                _hard_screening_result_rule(dict(rule))
                for rule in list(row.rule_results_json or [])
                if isinstance(rule, dict)
            ],
            "updatedAt": row.updated_at.isoformat(),
        }

    def review(
        self,
        user: User,
        app: Application,
        decision: str,
        reason: str,
    ) -> dict[str, Any]:
        authorization_action = (
            "review_hard_screening_pass"
            if decision == "pass"
            else "review_hard_screening_reject"
        )
        # Router 的 CommandRunner 是正常入口；服务层仍执行同一条资源授权，
        # 防止内部调用绕过申请可见性、部门范围或决策方向权限。
        AuthorizationService(self.db).require_application_action(
            user, app, authorization_action
        )
        if app.status != "hard_screening_review":
            raise BusinessError(
                "hard_screening_review_state_invalid",
                "当前申请不处于硬筛人工复核状态",
                status_code=409,
            )
        effective_time = _now()
        business_timezone = "Asia/Shanghai"
        transition = ApplicationProcessService.plan_transition(
            app, action=authorization_action
        )
        reviewed_status = "passed" if decision == "pass" else "failed"
        app.hard_screening_status = reviewed_status
        app.hard_screening_summary = reason
        app.rejection_stage = "hard_screening" if decision == "reject" else None
        clear_application_recovery(app)
        result_row = self.db.scalar(
            select(HardScreeningResult)
            .where(
                HardScreeningResult.application_id == app.application_id,
                HardScreeningResult.policy_id == app.hard_screening_policy_id,
            )
            .order_by(HardScreeningResult.updated_at.desc())
        )
        if result_row is not None:
            # Application 是流程状态，HardScreeningResult 是正式结论。人工复核
            # 必须原子更新两者，否则列表 DTO 会继续读到旧的 manual_review。
            # 自动结论保存在 automated_* 字段，HumanDecision 另存完整审计记录。
            reviewed_rules: list[dict[str, Any]] = []
            for raw_rule in list(result_row.rule_results_json or []):
                rule = dict(raw_rule) if isinstance(raw_rule, dict) else {}
                if str(rule.get("status") or "") == "manual_review":
                    rule = {
                        **rule,
                        "automated_status": rule.get("status"),
                        "automated_reason": rule.get("reason"),
                        "status": reviewed_status,
                        "reason": f"人工复核：{reason}",
                        "reason_code": "human_override",
                    }
                reviewed_rules.append(rule)
            result_row.status = reviewed_status
            result_row.summary = reason
            result_row.rule_results_json = reviewed_rules
            result_row.updated_at = effective_time
        self.db.add(
            HumanDecision(
                decision_id=_id("HD"),
                application_id=app.application_id,
                actor_role=user.role,
                actor_name=user.display_name,
                decision=f"hard_screening_{decision}",
                from_status=transition.from_status,
                to_status=transition.to_status,
                reason=reason,
                effective_at=effective_time,
                business_timezone=business_timezone,
                created_at=_now(),
            )
        )
        self._stage(
            app,
            user,
            transition.action,
            transition.from_status,
            transition.to_status,
            reason,
            effective_at=effective_time,
            business_timezone=business_timezone,
        )
        ApplicationProcessService.apply_transition(app, transition, now=effective_time, owner="HR")
        if decision == "pass":
            # 不能在外层 CommandRunner handler 中再次调用会 commit 的公开命令。
            # 这里复用无提交的内部入队方法，使复核结论和评分任务原子落库。
            ScoringRequestCommands(self.db).enqueue_from_hard_screening_review(
                app=app,
                user=user,
            )
        record_audit_event(
            self.db,
            actor=user,
            action="hard_screening.review",
            target_type="application",
            target_id=app.application_id,
            summary=f"人工硬筛复核：{'通过' if decision == 'pass' else '拒绝'}",
            details={
                "fromStatus": transition.from_status,
                "toStatus": transition.to_status,
                "reason": reason,
            },
        )
        return {
            "applicationId": app.application_id,
            "status": app.status,
            "hardScreeningStatus": app.hard_screening_status or "not_configured",
        }

    @staticmethod
    def _policy_json(policy: HardScreeningPolicy) -> dict[str, Any]:
        metadata = dict(policy.rules_json or {})
        return {
            "jobId": policy.job_id,
            "policyId": policy.policy_id,
            "version": policy.version,
            "enabled": policy.enabled,
            "rules": list(metadata.get("items") or []),
            "generationMode": metadata.get("generation_mode"),
            "generationStatus": metadata.get("generation_status"),
            "sourceJdVersionId": metadata.get("source_jd_version_id"),
        }

    def _stage(
        self,
        app: Application,
        user: User,
        action: str,
        from_status: str,
        to_status: str,
        note: str,
        *,
        effective_at: datetime | None = None,
        business_timezone: str = "Asia/Shanghai",
    ) -> None:
        self.db.add(
            StageHistory(
                stage_history_id=_id("SH"),
                application_id=app.application_id,
                actor_role=user.role,
                actor_name=user.display_name,
                action=action,
                from_status=from_status,
                to_status=to_status,
                note=note,
                effective_at=effective_at or _now(),
                business_timezone=business_timezone,
                created_at=_now(),
            )
        )
