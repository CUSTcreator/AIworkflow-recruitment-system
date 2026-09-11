from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime

from backend.app.shared.errors import BusinessRuleError
from backend.app.shared.workflows.process_view import load_workflow_processes
from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from backend.app.modules.jobs.management_schemas import (
    ImportRecordListView,
    ImportRecordView,
    JobManagementView,
)
from backend.app.models.entities import (
    Application,
    ApplicationAssessmentVersion,
    Candidate,
    Department,
    Job,
    JobDocumentImport,
    JobDraft,
    JobRequirementProfileRecord,
    JobVersionRecord,
    NotificationReadCursor,
    ResumeSubmission,
    SourceDocument,
    Task,
    User,
    WorkflowRun,
)
from backend.app.modules.auth.public import AuthorizationService, ScopeService
from backend.app.modules.candidates.public import CandidateIntakeService
from backend.app.shared.recovery_actions import RecoveryActionSpec, public_recovery_actions
from backend.app.modules.jobs.job_recovery import job_recovery_actions, job_recovery_plan
from backend.app.modules.jobs.profile_readiness import evaluate_job_profile_record
from backend.app.modules.jobs.configuration_status import missing_job_assignments


JOB_PROFILE_RECOVERY_ACTIONS = {
    "retry_profile": RecoveryActionSpec(
        "retry_profile", "重试生成岗位画像", retry_scope="job_profile_compilation"
    ),
    "regenerate_profile": RecoveryActionSpec(
        "regenerate_profile", "重新生成岗位画像", retry_scope="job_profile_compilation"
    ),
}


IMPORT_RECOVERY_ACTIONS = {
    "retry_job_import": RecoveryActionSpec(
        "retry_job_import", "重新处理", retry_scope="job_document_import"
    ),
    "review_job_import": RecoveryActionSpec(
        "review_job_import", "继续确认岗位", requires_input=True,
        retry_scope="confirm_job_drafts",
    ),
}


class RecruitmentManagementQueryService:
    """HR business read models; technical workflow details stay in admin monitoring."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def _require_import_access(self, user: User, import_type: str | None) -> None:
        if import_type == "resume":
            AuthorizationService(self.db).require_business_action(user, "resume.upload")
            return
        if import_type == "job":
            AuthorizationService(self.db).require_business_action(
                user, "job_document.upload", require_organization_scope=True
            )
            return
        if not (
            AuthorizationService(self.db).can_business_action(user, "resume.upload")
            or AuthorizationService(self.db).can_business_action(
                user, "job_document.upload", require_organization_scope=True
            )
        ):
            raise BusinessRuleError(status_code=403, detail="无权查看导入记录")

    def list_jobs(self, user: User) -> list[JobManagementView]:
        authorization = AuthorizationService(self.db)
        # 岗位列表是普通业务读取：只按账号有效状态和数据范围过滤。导入 JD、
        # 编辑岗位、配置硬筛及删除等敏感动作在 DTO 投影和命令端分别校验职责权限。
        authorization.require_data_view(user)
        scope = ScopeService(authorization).data_scope(user)
        conditions = [Job.deleted_at.is_(None)]
        if scope.business_scope != "organization":
            # 在 SQL 查询阶段过滤，避免跨部门岗位及其候选人数、画像状态泄露。
            conditions.append(Job.department_id == scope.department_id)
        jobs = list(
            self.db.scalars(
                select(Job).where(*conditions).order_by(
                    (Job.status != "open"),
                    Job.opened_at.desc(),
                    Job.title,
                )
            )
        )
        if not jobs:
            return []
        job_ids = [job.job_id for job in jobs]
        departments = {
            row.department_id: row.name
            for row in self.db.scalars(select(Department))
        }
        managers = {
            row.user_id: row.display_name
            for row in self.db.scalars(select(User))
        }
        candidate_counts = dict(
            self.db.execute(
                select(Application.job_id, func.count(Application.application_id))
                .where(
                    Application.job_id.in_(job_ids),
                    Application.deleted_at.is_(None),
                )
                .group_by(Application.job_id)
            ).all()
        )
        document_ids = {
            job.source_document_id
            for job in jobs
            if job.source_document_id
        }
        documents = {
            row.source_document_id: row
            for row in self.db.scalars(
                select(SourceDocument).where(
                    SourceDocument.source_document_id.in_(document_ids)
                )
            )
        } if document_ids else {}
        versions: dict[str, JobVersionRecord] = {}
        for version in self.db.scalars(
            select(JobVersionRecord)
            .where(JobVersionRecord.job_id.in_(job_ids))
            .order_by(JobVersionRecord.job_id, JobVersionRecord.version.desc())
        ):
            versions.setdefault(version.job_id, version)
        active_profile_ids = {
            str(version.active_job_profile_id)
            for version in versions.values()
            if version.active_job_profile_id
        }
        active_profiles = {
            profile.job_profile_id: profile
            for profile in self.db.scalars(
                select(JobRequirementProfileRecord).where(
                    JobRequirementProfileRecord.job_profile_id.in_(active_profile_ids)
                )
            )
        } if active_profile_ids else {}
        waiting_counts = dict(
            self.db.execute(
                select(Application.job_id, func.count(Application.application_id))
                .where(
                    Application.job_id.in_(job_ids),
                    Application.deleted_at.is_(None),
                    Application.status == "waiting_job_profile",
                )
                .group_by(Application.job_id)
            ).all()
        )
        return [
            self._job_view(
                job,
                departments=departments,
                managers=managers,
                candidate_count=int(candidate_counts.get(job.job_id, 0)),
                documents=documents,
                job_version=versions.get(job.job_id),
                active_profile=active_profiles.get(
                    str(versions[job.job_id].active_job_profile_id)
                ) if job.job_id in versions and versions[job.job_id].active_job_profile_id else None,
                waiting_application_count=int(waiting_counts.get(job.job_id, 0)),
                can_edit=authorization.can_business_action(
                    user, "job.edit", department_id=job.department_id
                ),
            )
            for job in jobs
        ]

    def get_job(self, user: User, job_id: str) -> JobManagementView:
        for item in self.list_jobs(user):
            if item.job_id == job_id:
                return item
        raise BusinessRuleError(status_code=404, detail="岗位不存在")

    def list_import_records(
        self,
        user: User,
        *,
        import_type: str | None,
        status: str | None,
        limit: int,
    ) -> ImportRecordListView:
        self._require_import_access(user, import_type)
        records: list[ImportRecordView] = []
        if import_type in {None, "job"}:
            if AuthorizationService(self.db).can_business_action(
                user, "job_document.upload", require_organization_scope=True
            ):
                records.extend(self._job_import_records())
        if import_type in {None, "resume"}:
            if AuthorizationService(self.db).can_business_action(user, "resume.upload"):
                records.extend(self._resume_import_records(user))
        if status:
            records = [item for item in records if item.status == status]
        records.sort(key=lambda item: item.created_at, reverse=True)
        unread_action_count = self._unread_action_count(
            user=user,
            records=records,
            import_type=import_type,
        )
        total = len(records)
        return ImportRecordListView(
            items=records[:limit],
            total=total,
            unread_action_count=unread_action_count,
        )

    def mark_import_records_read(
        self, user: User, import_type: str, *, commit: bool = True
    ) -> NotificationReadCursor:
        self._require_import_access(user, import_type)
        cursor = self.db.get(
            NotificationReadCursor,
            {"user_id": user.user_id, "channel": import_type},
        )
        if cursor is None:
            cursor = NotificationReadCursor(
                user_id=user.user_id,
                channel=import_type,
                last_read_at=datetime.now(UTC).replace(tzinfo=None),
            )
            self.db.add(cursor)
        else:
            cursor.last_read_at = datetime.now(UTC).replace(tzinfo=None)
        # “已读”也是命令写入，提交由调用它的 CommandRunner 统一完成。
        self.db.flush()
        return cursor

    def _unread_action_count(
        self,
        *,
        user: User,
        records: list[ImportRecordView],
        import_type: str | None,
    ) -> int:
        action_records = [
            item for item in records
            if item.available_actions
        ]
        if not action_records:
            return 0
        cursors = {
            row.channel: row.last_read_at
            for row in self.db.scalars(
                select(NotificationReadCursor).where(
                    NotificationReadCursor.user_id == user.user_id
                )
            )
        }
        return sum(
            1
            for item in action_records
            if item.updated_at > cursors.get(item.import_type, datetime.min)
            and (import_type is None or item.import_type == import_type)
        )

    def navigation_notification_counts(self, user: User) -> tuple[int, int]:
        now = datetime.now(UTC).replace(tzinfo=None)
        cursors = {
            row.channel: row
            for row in self.db.scalars(
                select(NotificationReadCursor).where(
                    NotificationReadCursor.user_id == user.user_id,
                    NotificationReadCursor.channel.in_(("task", "candidate_application")),
                )
            )
        }
        for channel in ("task", "candidate_application"):
            if channel not in cursors:
                cursors[channel] = NotificationReadCursor(
                    user_id=user.user_id,
                    channel=channel,
                    last_read_at=now,
                )

        authorization = AuthorizationService(self.db)
        # 导航数字是候选人/申请数据的派生信息，按账号有效状态和数据范围过滤。
        if not authorization.access_context(user).is_active:
            return 0, 0
        scope = ScopeService(authorization).data_scope(user)
        application_filters = [Application.deleted_at.is_(None)]
        if scope.business_scope != "organization":
            application_filters.append(Application.department_id == scope.department_id)

        task_count = int(
            self.db.scalar(
                select(func.count())
                .select_from(Task)
                .join(
                    Application,
                    Application.application_id == Task.application_id,
                )
                .where(
                    Task.assignee_user_id == user.user_id,
                    Task.status != "done",
                    Task.task_type != "run_scoring",
                    Task.created_at > cursors["task"].last_read_at,
                    *application_filters,
                )
            )
            or 0
        )
        candidate_query = select(func.count()).select_from(Application).where(
            Application.submitted_at > cursors["candidate_application"].last_read_at,
            *application_filters,
        )
        candidate_count = int(self.db.scalar(candidate_query) or 0)
        return task_count, candidate_count

    def mark_navigation_channel_read(
        self,
        user: User,
        channel: str,
        *,
        commit: bool = True,
    ) -> NotificationReadCursor:
        cursor = self.db.get(
            NotificationReadCursor,
            {"user_id": user.user_id, "channel": channel},
        )
        if cursor is None:
            cursor = NotificationReadCursor(
                user_id=user.user_id,
                channel=channel,
                last_read_at=datetime.now(UTC).replace(tzinfo=None),
            )
            self.db.add(cursor)
        else:
            cursor.last_read_at = datetime.now(UTC).replace(tzinfo=None)
        # “已读”也是命令写入，提交由调用它的 CommandRunner 统一完成。
        self.db.flush()
        return cursor

    def _job_import_records(self) -> list[ImportRecordView]:
        imports = list(self.db.execute(
            select(JobDocumentImport, SourceDocument)
            .join(
                SourceDocument,
                SourceDocument.source_document_id == JobDocumentImport.source_document_id,
            )
            .where(SourceDocument.document_type == "job_requirement")
            .order_by(JobDocumentImport.created_at.desc())
        ))
        if not imports:
            return []
        document_ids = [document.source_document_id for _, document in imports]
        import_ids = [item.job_document_import_id for item, _ in imports]
        latest_runs: dict[str, WorkflowRun] = {}
        for run in self.db.scalars(
            select(WorkflowRun)
            .where(
                WorkflowRun.workflow_type == "job_document_import_workflow",
                WorkflowRun.subject_type == "job_document_import",
                WorkflowRun.subject_id.in_(import_ids),
            )
            .order_by(WorkflowRun.subject_id, WorkflowRun.updated_at.desc(), WorkflowRun.started_at.desc())
        ).all():
            latest_runs.setdefault(run.subject_id, run)
        processes = load_workflow_processes(
            self.db, {run.workflow_run_id for run in latest_runs.values()}
        )
        drafts_by_document: dict[str, list[JobDraft]] = defaultdict(list)
        for draft in self.db.scalars(
            select(JobDraft).where(
                JobDraft.source_document_id.in_(document_ids),
                JobDraft.status != "deleted",
            )
        ):
            drafts_by_document[draft.source_document_id].append(draft)
        records: list[ImportRecordView] = []
        for import_task, document in imports:
            drafts = drafts_by_document.get(document.source_document_id, [])
            confirmed = [item for item in drafts if item.confirmed_job_id]
            unresolved = [item for item in drafts if not item.department_id]
            business_status = self._job_import_status(import_task.status)
            records.append(
                ImportRecordView(
                    import_id=document.source_document_id,
                    import_type="job",
                    filename=document.original_filename,
                    display_name=document.original_filename,
                    status=business_status,
                    result_summary=self._job_import_summary(
                        import_task.status,
                        len(drafts),
                        len(confirmed),
                        len(unresolved),
                    ),
                    target_ids=[
                        item.confirmed_job_id
                        for item in confirmed
                        if item.confirmed_job_id
                    ],
                    error_message=import_task.error_message,
                    process=(
                        processes[latest_runs[import_task.job_document_import_id].workflow_run_id].to_public_dict()
                        if import_task.job_document_import_id in latest_runs else None
                    ),
                    available_actions=self._job_import_actions(
                        business_status,
                        import_task=import_task,
                        document=document,
                    ),
                    created_at=import_task.created_at,
                    updated_at=import_task.updated_at,
                )
            )
        return records

    def _resume_import_records(self, user: User) -> list[ImportRecordView]:
        query = (
            select(ResumeSubmission, SourceDocument, Job)
            .outerjoin(
                SourceDocument,
                SourceDocument.source_document_id == ResumeSubmission.source_document_id,
            )
            .outerjoin(Job, Job.job_id == ResumeSubmission.job_id)
            .order_by(ResumeSubmission.created_at.desc())
        )
        scope = ScopeService(AuthorizationService(self.db)).data_scope(user)
        if scope.business_scope != "organization":
            query = query.where(
                or_(
                    Job.department_id == scope.department_id,
                    exists(
                        select(Application.application_id).where(
                            Application.candidate_id == ResumeSubmission.candidate_id,
                            Application.department_id == scope.department_id,
                            Application.deleted_at.is_(None),
                        )
                    ),
                )
            )
        rows = self.db.execute(query).all()
        candidate_ids = {submission.candidate_id for submission, _, _ in rows if submission.candidate_id}
        candidates = {
            row.candidate_id: row
            for row in self.db.scalars(select(Candidate).where(Candidate.candidate_id.in_(candidate_ids))).all()
        } if candidate_ids else {}
        # 简历记录允许因任一关联申请可见而进入列表，但其余关联申请仍必须逐条
        # 过滤。这里复用 CandidateIntakeService，防止导入页与候选人导入页形成
        # 两套不一致、且容易遗漏的跨部门投影逻辑。
        intake_service = CandidateIntakeService(self.db)
        applications_by_candidate: dict[str, list[Application]] = {
            candidate_id: [
                application
                for application, _, _ in intake_service.visible_application_rows(
                    user=user,
                    candidate_id=candidate_id,
                )
            ]
            for candidate_id in candidate_ids
        }
        application_ids = {
            application.application_id
            for applications in applications_by_candidate.values()
            for application in applications
        }
        screening_status = self._screening_statuses(application_ids)
        records: list[ImportRecordView] = []
        authorization = AuthorizationService(self.db)
        for submission, document, job in rows:
            candidate = candidates.get(submission.candidate_id or "")
            applications = applications_by_candidate.get(submission.candidate_id or "", [])
            application = next(
                (item for item in applications if item.application_id == submission.application_id),
                applications[0] if len(applications) == 1 else None,
            )
            # ResumeSubmission 也可能保存原始目标 Job。该 Job 必须在当前账号的
            # 数据范围内才能出现在导入记录里；不能因为同一 Candidate 的另一条
            # 申请可见就连带公开它。
            visible_submission_job = (
                job
                if job is not None
                and authorization.can_view_data_in_department(user, job.department_id)
                else None
            )
            metadata = dict(submission.structure_metadata_json or {})
            stored_name = (candidate.display_name if candidate else "").strip()
            extracted_name = str(metadata.get("candidate_name") or "").strip()
            candidate_name = (
                extracted_name
                if stored_name in {"", "待解析候选人", "待确认候选人"}
                else stored_name
            ) or extracted_name or (submission.candidate_name_override or "").strip()
            business_status = self._resume_import_status(submission.status)
            job_title = visible_submission_job.title if visible_submission_job is not None else (
                "待专业匹配" if not applications else f"已匹配 {len(applications)} 个岗位"
            )
            records.append(
                ImportRecordView(
                    import_id=submission.resume_submission_id,
                    import_type="resume",
                    filename=(document.original_filename if document else "原始简历文件已缺失"),
                    display_name=(
                        f"{candidate_name} - {job_title}"
                        if candidate_name
                        else f"待识别（{document.original_filename if document else '原始简历文件已缺失'}） - {job_title}"
                    ),
                    status=business_status,
                    result_summary=self._resume_import_summary(business_status),
                    job_id=(
                        visible_submission_job.job_id
                        if visible_submission_job is not None
                        else (application.job_id if application else None)
                    ),
                    job_title=job_title,
                    application_id=application.application_id if application else None,
                    target_ids=[item.application_id for item in applications],
                    screening_status=screening_status.get(
                        application.application_id if application else "",
                        "pending" if (application is not None or submission.application_id is not None) else "not_started",
                    ),
                    error_message=(
                        submission.error_message
                        or ("原始简历文件已缺失，请重新上传" if document is None else None)
                    ),
                    available_actions=intake_service._available_actions(
                        submission,
                        document=document,
                        user=user,
                        is_current=bool(
                            candidate is not None
                            and candidate.current_resume_submission_id
                            == submission.resume_submission_id
                        ),
                    ),
                    created_at=submission.created_at,
                    updated_at=submission.updated_at,
                )
            )
        return records

    @staticmethod
    def _job_import_actions(
        status: str,
        *,
        import_task: JobDocumentImport | None = None,
        document: SourceDocument | None = None,
    ) -> list[dict[str, object]]:
        """岗位导入动作由恢复码和已落库工件共同决定，前端不再猜状态。"""
        if status not in {"failed", "review_required"}:
            return []
        if document is None:
            # 兼容内部历史调用；真实页面总会传入 SourceDocument，并使用细粒度恢复计划。
            codes = ["retry_job_import"] if status == "failed" else ["review_job_import"] if status == "review_required" else []
            return public_recovery_actions(codes, IMPORT_RECOVERY_ACTIONS)
        code = import_task.recovery_code if import_task is not None else None
        if not code:
            if status == "failed":
                code = "job_extraction_retryable"
            elif status == "review_required":
                code = "job_draft_review_required"
        plan = job_recovery_plan(
            code,
            has_extraction=bool(document and (document.parsed_text or document.document_blocks_ref)),
        )
        actions = job_recovery_actions(list(plan.actions))
        # 兼容尚未升级的前端动作编码；新客户端只会消费上面的专用编码。
        if status == "failed" and not any(item["action"] == "retry_job_import" for item in actions):
            actions.append(public_recovery_actions(["retry_job_import"], IMPORT_RECOVERY_ACTIONS)[0])
        if status == "review_required" and any(item["action"] == "review_job_drafts" for item in actions):
            actions.append(public_recovery_actions(["review_job_import"], IMPORT_RECOVERY_ACTIONS)[0])
        return actions

    def _screening_statuses(self, application_ids: set[str]) -> dict[str, str]:
        if not application_ids:
            return {}
        result: dict[str, str] = {}
        assessments = self.db.scalars(
            select(ApplicationAssessmentVersion)
            .where(
                ApplicationAssessmentVersion.application_id.in_(application_ids),
                ApplicationAssessmentVersion.stage == "screening",
                ApplicationAssessmentVersion.published_at.is_not(None),
            )
            .order_by(
                ApplicationAssessmentVersion.application_id,
                ApplicationAssessmentVersion.version.desc(),
                ApplicationAssessmentVersion.created_at.desc(),
            )
        ).all()
        for item in assessments:
            result.setdefault(item.application_id, "completed")
        runs = self.db.scalars(
            select(WorkflowRun).where(
                WorkflowRun.application_id.in_(application_ids),
                WorkflowRun.workflow_type == "scoring_workflow",
                WorkflowRun.status.in_(("pending", "running")),
            )
        ).all()
        for run in runs:
            if run.application_id:
                result[run.application_id] = run.status
        return result

    @staticmethod
    def _job_view(
        job: Job,
        *,
        departments: dict[str, str],
        managers: dict[str, str],
        candidate_count: int,
        documents: dict[str, SourceDocument],
        job_version: JobVersionRecord | None,
        active_profile: JobRequirementProfileRecord | None,
        waiting_application_count: int,
        can_edit: bool,
    ) -> JobManagementView:
        source_document_id = job.source_document_id
        missing_assignments = (
            missing_job_assignments(
                hiring_manager_id=job.hiring_manager_id,
                department_recruiter_id=job.department_recruiter_id,
            )
            if job.status == "setup_pending"
            else []
        )
        document = documents.get(source_document_id) if source_document_id else None
        profile_actions: list[dict[str, object]] = []
        profile_readiness = evaluate_job_profile_record(
            active_profile,
            job_id=job.job_id,
            jd_version_id=job_version.jd_version_id if job_version else "",
        ) if job_version is not None else None
        profile_available = bool(profile_readiness and profile_readiness.ready)
        profile_json = dict(active_profile.profile_json or {}) if active_profile else {}
        profile_degraded = bool(profile_json.get("degraded"))
        profile_quality_message = None
        if profile_degraded:
            profile_quality_message = (
                "部分岗位能力由本地规则降级生成，请检查岗位要求和画像结果；"
                "需要时可重新生成。"
            )
        # profile_status 是流程事实，画像内容是否能进入 V1 则由统一 readiness
        # 判定决定。兼容历史数据中“status=ready 但空画像”的情况，避免页面误报就绪。
        effective_profile_status = (
            "review_required"
            if job_version is not None
            and str(job_version.profile_status or "") == "ready"
            and not profile_available
            else str(job_version.profile_status)
            if job_version is not None
            else "not_started"
        )
        if job_version and can_edit:
            if effective_profile_status in {"failed", "review_required"}:
                fallback_code = (
                    "job_profile_review_required"
                    if effective_profile_status == "review_required"
                    else "job_profile_retryable"
                )
                profile_actions = job_recovery_actions(list(job_recovery_plan(
                    job_version.recovery_code or fallback_code,
                    has_profile=bool(job_version.active_job_profile_id),
                ).actions))
                # “校正岗位画像”复用岗位源要求编辑器：保存后会创建新的冻结版本，
                # 并重新进入画像编排，因此该动作必须原样下发给前端。
            elif profile_available:
                profile_actions = job_recovery_actions(["regenerate_job_profile"])
        return JobManagementView(
            job_id=job.job_id,
            title=job.title,
            department_id=job.department_id,
            department_name=departments.get(job.department_id, ""),
            headcount=job.headcount,
            status=job.status,
            hiring_manager_name=managers.get(job.hiring_manager_id or "") or None,
            department_recruiter_name=managers.get(job.department_recruiter_id or "") or None,
            job_configuration_status=(
                "incomplete" if missing_assignments else "complete"
            ),
            missing_job_assignments=missing_assignments,
            candidate_count=candidate_count,
            source_document_id=document.source_document_id if document else None,
            source_filename=document.original_filename if document else None,
            source_type="file_import" if document else "untracked",
            jd_text=job.jd_text or "",
            responsibilities=list(job.responsibilities or []),
            qualifications=list(job.qualifications or []),
            education_requirement=job.education_requirement,
            major_requirement=job.major_requirement,
            common_interview_template_id=job.common_interview_template_id,
            common_interview_template_name=None,
            inherits_default_interview_template=job.common_interview_template_id is None,
            opened_at=job.opened_at,
            closed_at=job.closed_at,
            jd_version_id=job_version.jd_version_id if job_version else None,
            jd_version=job_version.version if job_version else None,
            profile_status=effective_profile_status,
            active_job_profile_id=job_version.active_job_profile_id if job_version else None,
            profile_workflow_run_id=job_version.profile_workflow_run_id if job_version else None,
            profile_error_message=job_version.profile_error_message if job_version else None,
            profile_degraded=profile_degraded,
            profile_quality_message=profile_quality_message,
            recovery_code=job_version.recovery_code if job_version else None,
            recovery_context=dict(job_version.recovery_context_json or {}) if job_version else {},
            can_route_candidate=job.status in {"setup_pending", "open"} and job_version is not None,
            can_start_screening=profile_available,
            waiting_application_count=waiting_application_count,
            available_actions=profile_actions,
        )

    @staticmethod
    def _job_import_status(import_status: str) -> str:
        """将权威导入状态映射为列表分组，不再从草稿反推流程状态。"""
        if import_status == "failed":
            return "failed"
        if import_status in {"queued", "processing"}:
            return "processing"
        if import_status in {"review_required", "partially_confirmed"}:
            return "review_required"
        if import_status == "completed":
            return "completed"
        # 未知状态必须留在用户可见的待处理分组，不能误报完成。
        return "review_required"

    @staticmethod
    def _job_import_summary(
        import_status: str,
        total: int,
        confirmed: int,
        unresolved: int,
    ) -> str:
        if import_status == "failed":
            return "招聘要求解析失败"
        if total == 0:
            return "正在提取岗位信息"
        if unresolved:
            return f"识别{total}个岗位，{unresolved}个部门待确认"
        if confirmed == total:
            return f"已创建{confirmed}个岗位"
        return f"识别{total}个岗位，已确认{confirmed}个"

    @staticmethod
    def _resume_import_status(status: str) -> str:
        if status in {"queued", "parsing", "extracting"}:
            return "processing"
        if status == "structure_review_required":
            return "review_required"
        if status == "failed":
            return "failed"
        return "completed" if status == "completed" else status

    @staticmethod
    def _resume_import_summary(status: str) -> str:
        return {
            "processing": "正在解析并创建候选申请",
            "review_required": "简历结构需要人工复核",
            "failed": "简历导入失败",
            "duplicate_review_required": "检测到已有候选人，请选择覆盖旧简历或放弃本次导入",
            "duplicate_blocked": "该候选人仍在招聘流程中，不能上传新版简历",
            "completed": "候选申请已创建",
        }.get(status, status)
