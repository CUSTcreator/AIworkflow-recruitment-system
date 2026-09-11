from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.entities import Application, Candidate, CandidateProfile, Department, Job, JobRequirementProfileRecord, JobVersionRecord, ResumeProfileRecord, ResumeSubmission, SourceDocument, User, WorkflowRun
from backend.app.modules.applications.public import (
    CreateApplicationFromRouting,
    application_main_route,
    application_primary_action,
    create_application_from_routing,
    is_recruitment_in_progress,
)
from backend.app.modules.candidates.resume_submission_status import ResumeSubmissionStatus
from backend.app.modules.candidates.intake_process_service import CandidateIntakeProcessService, RoutingOutcomeStatus
from backend.app.modules.jobs.public import (
    get_routable_job_version,
    list_open_job_routing_versions,
    list_routable_job_versions,
)
from backend.app.modules.document_ingestion.public import ResumeProfileSchema
from backend.app.modules.candidates.resume_recovery import (
    resume_recovery_actions,
    resume_recovery_plan,
    resume_recovery_view,
)
from backend.app.modules.auth.public import AuthorizationService
from backend.app.modules.candidates.domain.resume_submission_state_machine import (
    ResumeRecoveryCode,
)
from backend.app.shared.errors import BusinessError
from backend.app.shared.workflows.process_view import workflow_process_view
from recruitment_ai_core.candidate_routing import (
    JobMajorRequirement,
    MajorRoutingError,
    manual_selection_decisions,
    route_candidate_major,
)
from recruitment_ai_core.resume_structuring import resume_ir_from_structure
from recruitment_ai_core.screening_scoring.work_unit_structurer import build_resume_profile


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16].upper()}"


def _summary_text(value: Any, *, maximum: int, fallback: str = "") -> str:
    """将结构化结果投影到定长展示列，而完整原文仍保留在 ResumeProfile payload。"""
    text = str(value or "").strip()
    return text[:maximum] if text else fallback


class CandidateIntakeService:
    """Candidate-scoped intake: persist one structured resume, then fan out applications."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def refresh_lifecycle(self, candidate_id: str | None) -> None:
        """兼容旧调用入口。

        Candidate 不再根据 Application 数量在 ``active/closed/routing`` 间切换；
        招聘进度属于 Application，简历进度属于当前 ResumeSubmission。本方法保留
        空实现，待历史调用方完成迁移后删除。
        """
        return

    def visible_application_rows(
        self,
        *,
        user: User,
        candidate_id: str,
    ) -> list[tuple[Application, Job, Department | None]]:
        """返回当前用户可见的候选人申请及其岗位投影。

        简历处理记录可以因为候选人的任一申请可见而被读取，但候选人可能同时
        投递多个部门。因此所有把 Candidate 的关联申请放进响应的代码必须通过
        此处逐项过滤，不能将 ``can_view_resume_submission`` 的“任一可见”误用为
        “全部关联申请可见”。
        """
        rows = self.db.execute(
            select(Application, Job, Department)
            .join(Job, Job.job_id == Application.job_id)
            .outerjoin(Department, Department.department_id == Application.department_id)
            .where(
                Application.candidate_id == candidate_id,
                Application.deleted_at.is_(None),
            )
            .order_by(Application.submitted_at, Application.application_id)
        ).all()
        authorization = AuthorizationService(self.db)
        return [
            (application, job, department)
            for application, job, department in rows
            if authorization.can_view_application(user, application)
        ]

    @staticmethod
    def application_view(
        application: Application,
        job: Job,
        department: Department | None,
        *,
        current_resume_submission_id: str | None,
    ) -> dict[str, Any]:
        """将已通过范围检查的申请转换为公共简历处理投影。"""
        return {
            "application_id": application.application_id,
            "job_id": job.job_id,
            "job_title": job.title,
            "department_id": application.department_id,
            "department_name": department.name if department is not None else "",
            "status": application.status,
            "rejection_stage": getattr(application, "rejection_stage", None),
            "main_route": application_main_route(
                application.application_id,
                application.status,
                getattr(application, "rejection_stage", None),
            ),
            "primary_action": application_primary_action(
                application.application_id,
                application.status,
                getattr(application, "rejection_stage", None),
            ),
            "submitted_at": application.submitted_at,
            "adopted_resume_submission_id": application.adopted_resume_submission_id,
            "uses_current_resume": bool(current_resume_submission_id) and (
                application.adopted_resume_submission_id == current_resume_submission_id
            ),
            "major_requirement": job.major_requirement,
        }

    def intake_view(self, submission: ResumeSubmission, *, user: User) -> dict[str, Any]:
        """组装当前用户可见、可执行的简历处理读模型。

        恢复按钮必须在这里结合业务状态、已有工件和当前用户权限生成；前端不能
        根据 ``submission_status`` 自行猜测，命令接口仍会在执行时再次鉴权。
        """
        candidate = self.db.get(Candidate, submission.candidate_id) if submission.candidate_id else None
        document = self.db.get(SourceDocument, submission.source_document_id)
        # Candidate 是申请归属的唯一业务入口。简历版本只标识某个申请采用哪份简历，
        # 不能决定该申请是否属于当前 Candidate，否则重新上传后会遗漏旧版本的申请。
        applications: list[dict[str, Any]] = []
        if candidate is not None:
            applications = [
                self.application_view(
                    application,
                    job,
                    department,
                    current_resume_submission_id=candidate.current_resume_submission_id,
                )
                for application, job, department in self.visible_application_rows(
                    user=user,
                    candidate_id=candidate.candidate_id,
                )
            ]
        else:
            # 只读兼容：早期历史 Submission 尚未关联 Candidate 时，保留旧 payload 的审计入口。
            legacy_ids = []
            if legacy_ids:
                legacy_rows = self.db.execute(
                    select(Application, Job, Department)
                    .join(Job, Job.job_id == Application.job_id)
                    .outerjoin(Department, Department.department_id == Application.department_id)
                    .where(
                        Application.application_id.in_(legacy_ids),
                        Application.deleted_at.is_(None),
                    )
                ).all()
                by_id = {app.application_id: (app, job, department) for app, job, department in legacy_rows}
                applications = [
                    self.application_view(
                        app,
                        job,
                        department,
                        current_resume_submission_id=None,
                    )
                    for item in legacy_ids
                    if (row := by_id.get(item)) is not None
                    for app, job, department in [row]
                    if AuthorizationService(self.db).can_view_application(user, app)
                ]
        run = self.db.scalar(
            select(WorkflowRun)
            .where(
                WorkflowRun.subject_type == "resume_submission",
                WorkflowRun.subject_id == submission.resume_submission_id,
                WorkflowRun.workflow_type.in_(
                    ("resume_document_import_workflow", "candidate_routing_workflow")
                ),
            )
            .order_by(WorkflowRun.started_at.desc(), WorkflowRun.workflow_run_id.desc())
        )
        process = workflow_process_view(self.db, run)
        routing = dict(getattr(submission, "routing_result_json", None) or {})
        candidate_profile = self.db.get(CandidateProfile, candidate.candidate_id) if candidate is not None else None
        is_current = bool(
            candidate is not None
            and candidate.current_resume_submission_id == submission.resume_submission_id
        )
        duplicate_target = self._duplicate_target_view(submission, user=user)
        available_actions = self._available_actions(
            submission,
            document=document,
            user=user,
            is_current=is_current,
        )
        return {
            "submission_id": submission.resume_submission_id,
            "submission_status": submission.status,
            # 这些字段是简历处理状态合同的一部分。页面不再需要猜测 payload
            # 中的私有键名，也不会把 Candidate 的业务生命周期误当成简历任务状态。
            "intake_mode": submission.intake_mode,
            "review_kind": submission.review_kind,
            "failure_kind": submission.failure_kind,
            "recovery_code": submission.recovery_code,
            "duplicate_target": duplicate_target,
            "candidate": {
                "candidate_id": candidate.candidate_id,
                "display_name": candidate.display_name,
                "status": candidate.status,
                "major": candidate_profile.major if candidate_profile is not None else "",
                "resume_profile_id": candidate.current_resume_profile_id or "",
            } if candidate is not None else None,
            "document": {
                "source_document_id": document.source_document_id,
                "filename": document.original_filename,
                "available": bool(
                    document.document_type == "resume" and document.object_ref
                ),
            } if document is not None else None,
            "workflow": {
                "workflow_run_id": run.workflow_run_id,
                "status": run.status,
                "error_message": run.error_message,
            } if run is not None else None,
            "process": process.to_public_dict() if process.workflow_run_id else None,
            "routing": routing,
            "routing_status": str(getattr(submission, "routing_status", None) or "idle"),
            "routing_reason": str(getattr(submission, "routing_reason", None) or "") or None,
            "applications": applications,
            "review_reason": submission.error_message
            or getattr(submission, "routing_reason", None)
            or None,
            "is_current": is_current,
            "superseded_by_submission_id": self._superseded_by_submission_id(submission.resume_submission_id),
            "recovery": self._recovery_view(
                submission,
                is_current=is_current,
                available_actions=available_actions,
            ),
            "processing_quality": self._processing_quality(submission),
            "available_actions": available_actions,
        }

    def _duplicate_target_view(
        self, submission: ResumeSubmission, *, user: User
    ) -> dict[str, Any] | None:
        """公开单一重复匹配真正指向的 Candidate，禁止用上传占位档案代替。"""
        identity_match = dict(
            (getattr(submission, "review_context_json", None) or {}).get(
                "identityMatch"
            )
            or {}
        )
        if identity_match.get("status") != "matched":
            return None
        candidate_id = str(identity_match.get("candidateId") or "").strip()
        if not candidate_id:
            return None
        authorization = AuthorizationService(self.db)
        if not authorization.can_candidate_material_view(user, candidate_id):
            return None
        candidate = self.db.get(Candidate, candidate_id)
        if candidate is None or candidate.status == "archived":
            return None
        applications = list(
            self.db.scalars(
                select(Application).where(
                    Application.candidate_id == candidate_id,
                    Application.deleted_at.is_(None),
                )
            )
        )
        review_kind = str(getattr(submission, "review_kind", None) or "")
        return {
            "candidate_id": candidate.candidate_id,
            "display_name": candidate.display_name,
            "application_count": len(applications),
            "active_application_count": sum(
                is_recruitment_in_progress(application.status)
                for application in applications
            ),
            # 替换当前简历会重建目标 Candidate 的全部进行中申请，因此 DTO 必须
            # 与执行端采用同一严格范围，不能先展示按钮再由接口返回 403。
            "can_replace": (
                review_kind != "duplicate_blocked"
                and authorization.can_candidate_material_scope(
                    user, candidate.candidate_id
                )
            ),
        }

    @staticmethod
    def _processing_quality(submission: ResumeSubmission) -> dict[str, Any]:
        """公开已持久化的质量结论，不向页面泄露模型调用轨迹。"""
        metadata = dict(getattr(submission, "structure_metadata_json", None) or {})
        quality = metadata.get("processing_quality")
        return dict(quality) if isinstance(quality, dict) else {}

    def _available_actions(
        self,
        submission: ResumeSubmission,
        *,
        document: SourceDocument | None,
        user: User,
        is_current: bool,
    ) -> list[dict[str, object]]:
        """Return actions that are executable for this user and persisted state."""
        if not is_current:
            return []
        action_codes = self._available_action_codes(
            submission,
            document_available=bool(
                document is not None
                and document.document_type == "resume"
                and document.object_ref
            ),
        )
        authorization = AuthorizationService(self.db)
        return resume_recovery_actions(
            [
                action
                for action in action_codes
                if authorization.can_resume_submission_action(user, submission, action)
                and (
                    action != "replace_duplicate_resume"
                    or bool(
                        (self._duplicate_target_view(submission, user=user) or {}).get(
                            "can_replace"
                        )
                    )
                )
            ]
        )

    @staticmethod
    def _available_action_codes(
        submission: ResumeSubmission, *, document_available: bool = True
    ) -> list[str]:
        """Resolve business state and artifacts to permission-independent actions.

        Keeping this decision pure makes every exceptional branch testable.  The
        caller applies current-user authorization before exposing these codes.
        """
        if not document_available:
            # 没有 SourceDocument 时重跑解析一定失败；新版 PDF 可以从当前 Candidate
            # 重新建立完整来源，因此只暴露这一条真正可执行的恢复路径。
            return ["upload_replacement_resume"]
        routing = dict(getattr(submission, "routing_result_json", None) or {})
        recovery_code = str(getattr(submission, "recovery_code", None) or "")
        has_parsed_text = bool(str(getattr(submission, "parsed_text", None) or "").strip())
        parse_metadata = dict(getattr(submission, "parse_result_json", None) or {})
        has_correction_blocks = bool(
            str(
                parse_metadata.get("documentBlocksRef")
                or parse_metadata.get("document_blocks_ref")
                or ""
            ).strip()
        )
        has_structure_result = bool(str(getattr(submission, "structure_result_ref", None) or "").strip())
        has_structure_hash = bool(str(getattr(submission, "structure_result_sha256", None) or "").strip())
        if recovery_code:
            actions = list(
                resume_recovery_plan(
                    recovery_code,
                    has_parsed_text=has_parsed_text,
                    has_structure_result=has_structure_result,
                    has_structure_hash=has_structure_hash,
                ).actions
            )
        else:
            actions = []
        if submission.status in {
            ResumeSubmissionStatus.COMPLETED.value,
            ResumeSubmissionStatus.REVIEW_REQUIRED.value,
            ResumeSubmissionStatus.FAILED.value,
        } and not recovery_code:
            # 普通“重新解析”故意创建新的解析尝试；只有发布重试和人工校正才会
            # 在新的子 Submission 中复用已经冻结、校验过的解析工件。这样既能
            # 避免旧终态被原地改写，也能让用户从最小必要步骤继续处理。
            actions.extend(["force_fresh_parse", "upload_replacement_resume"])
            if has_parsed_text and has_correction_blocks:
                actions.append("correct_parsed_resume")
        if not has_correction_blocks:
            # 人工校正必须读取稳定的解析 Block。只有整段文本却没有 Block 工件时，
            # 打开校正窗口必然失败，因此保留重新解析/上传而不展示死按钮。
            actions = [action for action in actions if action != "correct_parsed_resume"]
        # 简历已经完成解析/结构化后，人工选择岗位是正常业务操作；它不是数据
        # 待确认，也不依赖自动匹配是否命中。实际可投递岗位仍由接口过滤。
        if submission.status == ResumeSubmissionStatus.COMPLETED.value:
            # 岗位分发是已发布简历的下游状态，不能夺走结构化结果的校正入口。
            # 即使自动分发没有命中，用户仍可先校正 LLM 基础信息再重新匹配，
            # 或直接人工选岗继续硬筛和 V1。
            if has_parsed_text and has_correction_blocks:
                actions.append("correct_parsed_resume")
            return list(dict.fromkeys([*actions, "create_applications"]))
        if submission.status != ResumeSubmissionStatus.REVIEW_REQUIRED.value:
            return actions
        review_kind = str(getattr(submission, "review_kind", None) or "")
        if review_kind in {"duplicate", "duplicate_match", "duplicate_ambiguous"}:
            identity_match = dict((submission.review_context_json or {}).get("identityMatch") or {})
            if identity_match.get("status") == "ambiguous":
                return [*actions, "resolve_duplicate_candidates", "discard_submission"]
            return [*actions, "replace_duplicate_resume", "discard_submission"]
        if review_kind == "duplicate_blocked":
            return list(dict.fromkeys([*actions, "discard_submission"]))
        structure_status = str((getattr(submission, "structure_metadata_json", {}) or {}).get("structure_status") or "")
        if review_kind in {"structure", "structure_metadata", "structure_outline"} or structure_status == "review_required":
            # 结构化结果尚未确认时不得创建 Application，避免不完整事实进入后续评分链路。
            return list(dict.fromkeys(actions))
        # 除上述数据待确认分支外，历史“分发待确认”统一收敛为 HR 可主动选岗。
        return list(dict.fromkeys([*actions, "create_applications"]))

    def _superseded_by_submission_id(self, submission_id: str) -> str | None:
        successor = self.db.scalar(
            select(ResumeSubmission.resume_submission_id)
            .where(ResumeSubmission.supersedes_submission_id == submission_id)
            .order_by(ResumeSubmission.created_at.desc(), ResumeSubmission.resume_submission_id.desc())
        )
        return str(successor) if successor else None

    def _recovery_view(
        self,
        submission: ResumeSubmission,
        *,
        is_current: bool,
        available_actions: list[dict[str, object]],
    ) -> dict[str, Any]:
        if not is_current:
            return {
                "issue_code": "superseded",
                "display_message": "该版本已被新的简历处理任务替代",
                "allowed_actions": [],
            }
        routing = dict(getattr(submission, "routing_result_json", None) or {})
        recovery_code = str(getattr(submission, "recovery_code", None) or "")
        if recovery_code:
            view = resume_recovery_view(
                recovery_code,
                has_parsed_text=bool(str(submission.parsed_text or "").strip()),
                has_structure_result=bool(str(getattr(submission, "structure_result_ref", None) or "").strip()),
                has_structure_hash=bool(str(getattr(submission, "structure_result_sha256", None) or "").strip()),
                context=dict(getattr(submission, "recovery_context_json", None) or {}),
            )
            return {**view, "allowed_actions": available_actions}
        if submission.status == ResumeSubmissionStatus.FAILED.value:
            return {
                "issue_code": str(submission.failure_kind or "resume_processing_failed"),
                "mode": "user_recovery",
                "display_message": "处理未完成。可重新发起解析或上传替换文件。",
                "allowed_actions": available_actions,
            }
        if submission.status == ResumeSubmissionStatus.REVIEW_REQUIRED.value:
            return {
                "issue_code": str(submission.review_kind or "review_required"),
                "mode": "user_recovery",
                "display_message": "该简历需要处理后才能继续。",
                "allowed_actions": available_actions,
            }
        return {
            "issue_code": None,
            "mode": None,
            "display_message": None,
            "allowed_actions": available_actions,
        }

    def ensure_placeholder(self, submission: ResumeSubmission, document: SourceDocument, *, candidate_id: str | None = None) -> Candidate:
        # 首次上传已创建的占位 Candidate 在这里复用；仅处理历史数据缺少 Candidate 的兼容场景。
        if submission.candidate_id:
            existing = self.db.get(Candidate, submission.candidate_id)
            if existing is not None:
                return existing
        candidate = Candidate(
            candidate_id=candidate_id or _id("CAND"), display_name="待解析候选人",
            status="active",
            created_at=_now(), updated_at=_now(),
        )
        self.db.add(candidate)
        self.db.flush()
        submission.candidate_id = candidate.candidate_id
        submission.updated_at = _now()
        return candidate

    def persist_profile(
        self, *, candidate: Candidate, submission: ResumeSubmission, document: SourceDocument, structure_result: dict[str, Any], metadata: Any,
        force_new_version: bool = False,
    ) -> ResumeProfileRecord:
        # 将已通过校验的结构化结果持久化为 Candidate 下的新版 ResumeProfile，并保留
        # 来源文档用于追溯。任何未通过结构化或评分证据校验的结果都会在这里被拦截。
        resume_ir = resume_ir_from_structure(
            structure_result,
            candidate_id=candidate.candidate_id,
        )
        profile_contract = ResumeProfileSchema.model_validate(build_resume_profile(resume_ir))
        profile_data = profile_contract.model_dump(mode="json")
        profile_id = profile_contract.resume_profile_version_id
        latest = self.db.scalar(select(ResumeProfileRecord).where(ResumeProfileRecord.candidate_id == candidate.candidate_id).order_by(ResumeProfileRecord.version.desc()))
        if force_new_version:
            next_version = (latest.version + 1) if latest else 1
            profile_id = f"{profile_id}_R{next_version}"
            profile_data = {**profile_data, "resume_profile_version_id": profile_id}
            profile_contract = ResumeProfileSchema.model_validate(profile_data)
        profile = self.db.get(ResumeProfileRecord, profile_id)
        if profile is None:
            profile_source_sha = str(profile_contract.resume_raw_sha256 or document.source_sha256)
            if force_new_version:
                profile_source_sha = f"{profile_source_sha}:R{next_version}"
            profile = ResumeProfileRecord(
                resume_profile_id=profile_id, candidate_id=candidate.candidate_id,
                version=(latest.version + 1) if latest else 1,
                source_sha256=profile_source_sha,
                profile_schema_version=profile_contract.resume_profile_version,
                profile_json=profile_contract.model_dump(mode="json"),
            )
            self.db.add(profile)
            # Candidate.current_resume_profile_id 和 Submission.output_resume_profile_id
            # 均受数据库外键约束。先将新的画像记录写入数据库，再更新两个指针，
            # 避免 SQLAlchemy 在同一次 flush 中先 UPDATE Candidate 而导致外键失败。
            self.db.flush()
        # LLM/结构化结果是外部输入。完整内容已冻结在 ResumeProfile.profile_json；这里仅写
        # Candidate/Profile 的定长检索与展示列，不能让异常长文本破坏整个发布事务。
        candidate.display_name = _summary_text(
            metadata.candidate_name, maximum=128, fallback="待确认候选人"
        )
        CandidateIntakeProcessService.activate_candidate(candidate, now=_now())
        candidate_profile = self.db.get(CandidateProfile, candidate.candidate_id)
        if candidate_profile is None:
            candidate_profile = CandidateProfile(candidate_id=candidate.candidate_id)
            self.db.add(candidate_profile)
        profile_facts = dict(profile_contract.candidate_facts or {})
        education_records = [
            item
            for item in list(profile_facts.get("education_records") or [])
            if isinstance(item, dict)
        ]
        degree_rank = {
            "associate": 1, "bachelor": 2, "undergraduate": 2,
            "master": 3, "doctorate": 4,
        }
        degree_label = {
            "associate": "大专", "bachelor": "本科", "undergraduate": "本科",
            "master": "硕士", "doctorate": "博士", "other": "其他",
        }
        ordered_education = sorted(
            education_records,
            key=lambda item: (
                degree_rank.get(str(item.get("degree_level") or ""), 0),
                int(item.get("graduation_year") or 0),
            ),
            reverse=True,
        )
        primary_education = ordered_education[0] if ordered_education else {}
        education_summary = "；".join(
            " ".join(
                value
                for value in (
                    str(item.get("school") or "").strip(),
                    str(item.get("major") or "").strip(),
                    degree_label.get(str(item.get("degree_level") or ""), ""),
                )
                if value
            )
            for item in ordered_education
        )
        experience_fact = profile_facts.get("relevant_experience_years")
        experience_value = (
            experience_fact.get("value")
            if isinstance(experience_fact, dict)
            else experience_fact
        )
        candidate_profile.current_title = _summary_text(metadata.current_title, maximum=255)
        candidate_profile.years_of_experience = _summary_text(
            f"{float(experience_value):g}年" if isinstance(experience_value, (int, float)) else None,
            maximum=64,
        )
        candidate_profile.education = _summary_text(education_summary, maximum=255)
        candidate_profile.age = metadata.age
        candidate_profile.school = _summary_text(primary_education.get("school"), maximum=255)
        candidate_profile.major = _summary_text(primary_education.get("major"), maximum=255)
        candidate_profile.phone = _summary_text(getattr(metadata, "phone", None), maximum=64)
        candidate_profile.email = _summary_text(getattr(metadata, "email", None), maximum=255)
        candidate_profile.highest_degree = _summary_text(
            degree_label.get(str(primary_education.get("degree_level") or "")),
            maximum=64,
        )
        candidate_profile.graduation_year = (
            primary_education.get("graduation_year")
            if isinstance(primary_education.get("graduation_year"), int)
            else None
        )
        candidate_profile.updated_at = _now()
        submission.output_resume_profile_id = profile_id
        candidate.current_resume_profile_id = profile_id
        candidate.updated_at = _now()
        self.db.flush()
        return profile

    def prepare_routing_input(
        self,
        *,
        submission: ResumeSubmission,
        candidate: Candidate,
    ) -> dict[str, Any]:
        """读取已确认岗位版本，准备专业匹配输入。

        分发只依赖 JD 结构化字段；画像状态仅作为后续 Application 是否需要等待的
        附加信息，不能再把 queued/processing/failed 岗位从匹配候选集合中删掉。
        """
        profile_id = submission.output_resume_profile_id or candidate.current_resume_profile_id
        resume_profile = self.db.get(ResumeProfileRecord, profile_id) if profile_id else None
        facts = dict((resume_profile.profile_json or {}).get("candidate_facts") or {}) if resume_profile is not None else {}
        education_records = [
            item
            for item in list(facts.get("education_records") or [])
            if isinstance(item, dict)
        ]
        degree_rank = {
            "associate": 1, "bachelor": 2, "undergraduate": 2,
            "master": 3, "doctorate": 4,
        }
        ordered_education = sorted(
            education_records,
            key=lambda item: (
                degree_rank.get(str(item.get("degree_level") or ""), 0),
                int(item.get("graduation_year") or 0),
            ),
            reverse=True,
        )
        primary_education = next(
            (item for item in ordered_education if str(item.get("major") or "").strip()),
            {},
        )
        major = str(primary_education.get("major") or "").strip()
        major_source_quote = next(
            (
                str(ref.get("quote") or "")
                for ref in list(primary_education.get("source_refs") or [])
                if isinstance(ref, dict) and str(ref.get("quote") or "").strip()
            ),
            "",
        )
        # 分发仅接受已发布的结构化学历事实及其原文引用。不能回退到候选人
        # 摘要表的旧字段，否则模型会收到没有证据的专业文字。专业未识别时，
        # 后续会转为可人工选岗，而非猜测专业。
        requirements: list[dict[str, Any]] = []
        for job_version in list_open_job_routing_versions(self.db):
            requirements.append({
                "jobId": job_version.job_id,
                "jdVersionId": job_version.jd_version_id,
                "jobProfileId": job_version.job_profile_id,
                "profileStatus": job_version.profile_status,
                "title": job_version.title,
                "majorRequirement": job_version.major_requirement or "",
            })
        return {
            "status": "ready",
            "major": major,
            "majorSourceQuote": major_source_quote,
            "requirements": requirements,

        }
    @staticmethod
    def decide_routing(
        routing_input: dict[str, Any],
        *,
        source_quote: str = "",
    ) -> dict[str, Any]:
        """执行纯岗位匹配；外部模型异常必须原样抛给 StepRunner。"""
        if routing_input.get("status") == "waiting_for_job_profiles":
            return dict(routing_input)
        requirements = [
            JobMajorRequirement(
                str(item["jobId"]),
                str(item["jdVersionId"]),
                str(item["title"]),
                str(item.get("majorRequirement") or ""),
            )
            for item in routing_input.get("requirements") or []
            if isinstance(item, dict)
        ]
        try:
            decisions, trace = route_candidate_major(
                candidate_major=str(routing_input.get("major") or ""),
                source_quote=source_quote,
                jobs=requirements,
            )
        except MajorRoutingError as exc:
            # 结果矩阵损坏时仍能确定无专业限制岗位；其余岗位显式交给人工，
            # 而不是以空 decisions 让发布步骤丢掉全部岗位。
            return {
                "status": "completed",
                "major": str(routing_input.get("major") or ""),
                "reason": "自动专业匹配结果无效，需人工选择岗位",
                "decisions": manual_selection_decisions(
                    jobs=requirements,
                    reason="自动专业匹配结果无效，需人工选择岗位",
                ),
                "trace": {"mode": "routing_validation", "error": str(exc)},
                "requirements": routing_input.get("requirements") or [],
            }
        return {
            "status": "completed",
            "major": str(routing_input.get("major") or ""),
            "decisions": decisions,
            "trace": trace,
            "requirements": routing_input.get("requirements") or [],
        }

    def publish_routing(
        self,
        *,
        user: User,
        submission: ResumeSubmission,
        candidate: Candidate,
        profile: ResumeProfileRecord,
        document: SourceDocument,
        decision: dict[str, Any],
        routing_workflow_run_id: str,
    ) -> list[str]:
        """发布专业匹配结论，创建申请；画像未就绪的申请保持等待态。"""
        status = str(decision.get("status") or "")
        if status == "waiting_for_job_profiles":
            # 兼容旧 WorkflowArtifact；新流程不会再产生该分支。
            self._mark_routing_waiting(
                submission=submission,
                document=document,
                candidate=candidate,
                waiting_job_ids=[str(item) for item in decision.get("waitingJobIds") or []],
            )
            return []
        if status in {"review_required", "manual_selection_available"}:
            manual_reason = str(decision.get("reason") or "自动岗位匹配未形成结果，请人工选择岗位")
            CandidateIntakeProcessService.complete_with_routing(
                submission,
                routing_status=RoutingOutcomeStatus.MANUAL_SELECTION_AVAILABLE,
                reason=manual_reason,
                routing_extra={"trace": dict(decision.get("trace") or {})},
                recovery_code=ResumeRecoveryCode.ROUTING_MANUAL_SELECTION.value,
                recovery_context={"reason": manual_reason},
                document=document,
                candidate=candidate,
                now=_now(),
            )
            return []
        if status != "completed":
            raise ValueError("candidate_routing_decision_invalid")

        decisions = [item for item in decision.get("decisions") or [] if isinstance(item, dict)]
        matched_ids = [str(item.get("job_id") or "") for item in decisions if item.get("matched") and item.get("job_id")]
        requires_manual_selection = any(
            bool(item.get("requires_manual_selection")) for item in decisions
        )
        # routing_status / routing_reason 是阶段状态；routing_result_json 只保存稳定的
        # 业务结论。运行 trace 已由 Step/Activity Artifact 留存，不得混入业务结果。
        submission.routing_result_json = {
            "schema_version": "candidate_routing_result_v1",
            "major": str(decision.get("major") or ""),
            "results": decisions,
        }
        submission.routing_status = RoutingOutcomeStatus.PROCESSING.value
        submission.routing_reason = None
        submission.routing_workflow_run_id = routing_workflow_run_id
        if not matched_ids:
            CandidateIntakeProcessService.complete_with_routing(
                submission,
                routing_status=RoutingOutcomeStatus.MANUAL_SELECTION_AVAILABLE,
                reason="未自动匹配到可投递岗位，可人工选择岗位",
                routing_extra=dict(submission.routing_result_json or {}),
                recovery_code=ResumeRecoveryCode.ROUTING_MANUAL_SELECTION.value,
                recovery_context={"reason": "no_auto_match"},
                document=document,
                candidate=candidate,
                now=_now(),
            )
            return []

        frozen_versions = {
            str(item.get("jobId")): dict(item)
            for item in decision.get("requirements") or []
            if isinstance(item, dict) and item.get("jobId") and item.get("jdVersionId")
        }
        current_versions = {
            item.job_id: item for item in list_open_job_routing_versions(self.db)
        }
        application_ids: list[str] = []
        skipped: list[dict[str, str]] = []
        for job_id in matched_ids:
            frozen = frozen_versions.get(job_id)
            current = current_versions.get(job_id)
            if frozen is None or current is None or current.jd_version_id != str(frozen["jdVersionId"]):
                skipped.append({"job_id": job_id, "reason": "job_version_changed"})
                continue
            try:
                result = create_application_from_routing(
                    self.db,
                    command=CreateApplicationFromRouting(
                        candidate_id=candidate.candidate_id,
                        resume_submission_id=submission.resume_submission_id,
                        resume_profile_id=profile.resume_profile_id,
                        source_document_id=document.source_document_id,
                        job_id=current.job_id,
                        jd_version_id=current.jd_version_id,
                        job_profile_id=(
                            str(frozen["jobProfileId"])
                            if frozen.get("jobProfileId")
                            else None
                        ),
                        routing_workflow_run_id=routing_workflow_run_id,
                        idempotency_key=f"{submission.resume_submission_id}:{current.job_id}:{current.jd_version_id}",
                    ),
                    actor_id=user.user_id,
                )
                if result.application_id not in application_ids:
                    application_ids.append(result.application_id)
            except BusinessError as exc:
                skipped.append({"job_id": job_id, "reason": exc.code})
        submission.routing_result_json = {
            **dict(submission.routing_result_json or {}),
            "skipped": skipped,
        }
        if not application_ids:
            CandidateIntakeProcessService.complete_with_routing(
                submission,
                routing_status=RoutingOutcomeStatus.MANUAL_SELECTION_AVAILABLE,
                reason="自动创建申请未完成，可人工选择岗位",
                routing_extra=dict(submission.routing_result_json or {}),
                recovery_code=ResumeRecoveryCode.ROUTING_MANUAL_SELECTION.value,
                recovery_context={"reason": "application_creation_empty"},
                document=document,
                candidate=candidate,
                now=_now(),
            )
            return []
        if requires_manual_selection:
            # 已可靠匹配的岗位可以照常创建申请；其余岗位必须以待处理状态留在
            # 页面中，不能因“部分自动成功”被误标为整份简历均已自动分发。
            CandidateIntakeProcessService.complete_with_routing(
                submission,
                routing_status=RoutingOutcomeStatus.MANUAL_SELECTION_AVAILABLE,
                reason="部分岗位需人工选择后继续投递",
                routing_extra=dict(submission.routing_result_json or {}),
                recovery_code=ResumeRecoveryCode.ROUTING_MANUAL_SELECTION.value,
                recovery_context={"reason": "partial_manual_selection"},
                document=document,
                candidate=candidate,
                now=_now(),
            )
            return application_ids
        CandidateIntakeProcessService.complete_with_routing(
            submission,
            routing_status=RoutingOutcomeStatus.AUTO_MATCHED,
            reason="已按自动匹配创建岗位申请",
            document=document,
            candidate=candidate,
            now=_now(),
        )
        return application_ids
    def _has_ready_job_profile(self, job: Job, version: JobVersionRecord) -> bool:
        """确认岗位版本已经拥有可供 Application/V1 冻结使用的能力画像。"""
        profile_status = getattr(version, "profile_status", None)
        if profile_status is not None and profile_status != "ready":
            return False
        profile = self.db.scalar(
            select(JobRequirementProfileRecord).where(
                JobRequirementProfileRecord.job_id == job.job_id,
                JobRequirementProfileRecord.jd_version_id == version.jd_version_id,
                JobRequirementProfileRecord.source_sha256 == version.source_sha256,
            )
        )
        return profile is not None

    @staticmethod
    def _mark_routing_waiting(
        *,
        submission: ResumeSubmission,
        document: SourceDocument,
        candidate: Candidate,
        waiting_job_ids: list[str],
    ) -> None:
        """岗位尚在画像生成时发布等待结论，不把等待态写入 Candidate。"""
        CandidateIntakeProcessService.complete_with_routing(
            submission,
            routing_status=RoutingOutcomeStatus.WAITING_FOR_JOB_PROFILES,
            reason="候选人简历已处理完成，等待岗位能力画像生成后再进行分发",
            routing_extra={"waiting_job_ids": sorted(set(waiting_job_ids))},
            document=document,
            candidate=candidate,
            now=_now(),
        )

    def _manual_routing_context(
        self, submission: ResumeSubmission
    ) -> tuple[Candidate, ResumeProfileRecord, SourceDocument]:
        """允许结构化完成的 Candidate 人工选择岗位，排除真正的数据待确认分支。"""
        metadata = dict(submission.structure_metadata_json or {})
        if submission.status not in {
            ResumeSubmissionStatus.COMPLETED.value,
            ResumeSubmissionStatus.REVIEW_REQUIRED.value,
        }:
            raise ValueError("manual_routing_resume_not_completed")
        if str(getattr(submission, "review_kind", None) or "") in {"duplicate", "duplicate_match", "duplicate_ambiguous", "duplicate_blocked"}:
            raise ValueError("manual_routing_duplicate_review_not_supported")
        if str(metadata.get("structure_status") or "") == "review_required":
            raise ValueError("manual_routing_structure_review_not_confirmed")
        if not submission.candidate_id:
            raise ValueError("candidate_not_initialized")
        candidate = self.db.get(Candidate, submission.candidate_id)
        document = self.db.get(SourceDocument, submission.source_document_id)
        profile = self.db.get(
            ResumeProfileRecord, candidate.current_resume_profile_id
        ) if candidate is not None and candidate.current_resume_profile_id else None
        if candidate is None or profile is None or document is None:
            raise ValueError("candidate_routing_input_missing")
        return candidate, profile, document

    def manual_routing_options(
        self, *, submission: ResumeSubmission, user: User
    ) -> dict[str, Any]:
        """返回全部已确认岗位及其当前画像状态，前端据此明确展示可选原因。"""
        candidate, _, _ = self._manual_routing_context(submission)
        candidate_profile = self.db.get(CandidateProfile, candidate.candidate_id)
        authorization = AuthorizationService(self.db)
        jobs = [
            {
                "job_id": job.job_id,
                "title": job.title,
                "department_id": job.department_id,
                "department_name": job.department_name,
                "jd_version_id": job.jd_version_id,
                "job_status": job.job_status,
                "profile_status": job.profile_status or "queued",
                "job_profile_id": job.job_profile_id,
                "is_screening_ready": job.is_screening_ready,
                "selection_outcome": (
                    "start_screening"
                    if job.is_screening_ready
                    else "wait_job_profile"
                ),
            }
            for job in list_open_job_routing_versions(self.db)
            if authorization.can_business_action(
                user, "application.create", department_id=job.department_id
            )
        ]
        return {
            "candidate_id": candidate.candidate_id,
            "candidate_name": candidate.display_name,
            "candidate_major": candidate_profile.major if candidate_profile is not None else "",
            "jobs": jobs,
        }

    def manually_create_applications(
        self, *, user: User, submission: ResumeSubmission, job_ids: list[str]
    ) -> list[str]:
        """按 HR 明确选择创建申请；画像未完成时创建等待中的 Application。"""
        candidate, profile, document = self._manual_routing_context(submission)
        requested = list(dict.fromkeys(item.strip() for item in job_ids if item.strip()))
        if not requested:
            raise ValueError("manual_routing_job_required")
        all_open_versions = {item.job_id: item for item in list_open_job_routing_versions(self.db)}
        if any(job_id not in all_open_versions for job_id in requested):
            raise ValueError("manual_routing_job_invalid")
        application_ids: list[str] = []
        for job_id in requested:
            job_version = all_open_versions[job_id]
            AuthorizationService(self.db).require_business_action(
                user, "application.create", department_id=job_version.department_id
            )
            result = create_application_from_routing(
                self.db,
                command=CreateApplicationFromRouting(
                    candidate_id=candidate.candidate_id,
                    resume_submission_id=submission.resume_submission_id,
                    resume_profile_id=profile.resume_profile_id,
                    source_document_id=document.source_document_id,
                    job_id=job_version.job_id,
                    jd_version_id=job_version.jd_version_id,
                    job_profile_id=job_version.job_profile_id,
                    routing_workflow_run_id=f"manual-routing:{submission.resume_submission_id}",
                    idempotency_key=f"manual:{submission.resume_submission_id}:{job_version.job_id}:{job_version.jd_version_id}",
                ),
                actor_id=user.user_id,
            )
            if result.application_id not in application_ids:
                application_ids.append(result.application_id)
        CandidateIntakeProcessService.complete_with_routing(
            submission,
            routing_status=RoutingOutcomeStatus.MANUAL_SELECTED,
            reason="已按人工选择创建岗位申请",
            document=document,
            candidate=candidate,
            now=_now(),
        )
        return application_ids
