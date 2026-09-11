from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from backend.app.models.entities import Application, Candidate, CandidateProfile, NotificationReadCursor, ResumeSubmission, SourceDocument, User, WorkflowRun
from backend.app.modules.auth.public import AuthorizationService
from backend.app.modules.document_ingestion.public import ResumeDocumentService
from backend.app.shared.workflows.process_view import workflow_process_view

from .intake_service import CandidateIntakeService


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class CandidateIntakeQueryService:
    """Read models for the period from resume upload to Application creation."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.documents = ResumeDocumentService(db)
        self.intakes = CandidateIntakeService(db)
        self.authorization = AuthorizationService(db)

    def list(self, *, user: User, status: str = "all", keyword: str = "", page: int = 1, page_size: int = 30) -> dict:
        all_items = self._visible_items(user)
        counts = self._counts(user, all_items)
        text = keyword.strip().lower()
        items = [
            item for item in all_items
            if (status == "all" or item["bucket"] == status)
            and (not text or text in item["submission_id"].lower() or text in item["filename"].lower() or text in item["candidate_name"].lower()
                 or any(text in app["job_title"].lower() for app in item["applications"]))
        ]
        start = (max(page, 1) - 1) * max(page_size, 1)
        return {"items": items[start:start + max(page_size, 1)], "total": len(items), "page": max(page, 1), "page_size": max(page_size, 1), "counts": counts}

    def summary(self, *, user: User) -> dict:
        return self._counts(user, self._visible_items(user))

    def mark_read(self, *, user: User) -> datetime:
        cursor = self.db.get(NotificationReadCursor, {"user_id": user.user_id, "channel": "candidate_intake"})
        if cursor is None:
            cursor = NotificationReadCursor(user_id=user.user_id, channel="candidate_intake", last_read_at=_now())
            self.db.add(cursor)
        else:
            cursor.last_read_at = _now()
        self.db.flush()
        return cursor.last_read_at

    def _visible_items(self, user: User) -> list[dict]:
        """以 Candidate 为主行构建简历处理列表；当前 Submission 只表示该行的当前简历任务。"""
        # archived 是 Candidate 的软删除标记：简历处理页只展示仍有效的档案。
        candidates = self.db.scalars(
            select(Candidate)
            .where(Candidate.status != "archived")
            .order_by(Candidate.updated_at.desc(), Candidate.candidate_id.desc())
        ).all()
        current_submission_ids = [
            candidate.current_resume_submission_id
            for candidate in candidates
            if candidate.current_resume_submission_id
        ]
        legacy_candidate_ids = [
            candidate.candidate_id
            for candidate in candidates
            if not candidate.current_resume_submission_id
        ]
        submission_filters = [ResumeSubmission.candidate_id.is_(None)]
        if current_submission_ids:
            submission_filters.append(
                ResumeSubmission.resume_submission_id.in_(current_submission_ids)
            )
        if legacy_candidate_ids:
            # 只为未迁移历史 Candidate 读取其最近任务；新数据始终使用 current_resume_submission_id。
            submission_filters.append(ResumeSubmission.candidate_id.in_(legacy_candidate_ids))
        submissions = self.db.scalars(
            select(ResumeSubmission)
            .where(or_(*submission_filters))
            .order_by(ResumeSubmission.updated_at.desc(), ResumeSubmission.resume_submission_id.desc())
        ).all()
        submissions_by_id = {
            item.resume_submission_id: item
            for item in submissions
        }
        latest_by_candidate = self._latest_submissions_by_candidate(submissions)
        items: list[dict] = []

        for candidate in candidates:
            current_submission_id = str(candidate.current_resume_submission_id or "")
            submission = (
                submissions_by_id.get(current_submission_id)
                or latest_by_candidate.get(candidate.candidate_id)
            )
            if submission is None:
                continue
            if not self.authorization.can_view_resume_submission(user, submission):
                continue
            items.append(self._item_from_submission(submission, candidate, user=user))

        # 只有早期历史数据可能没有 Candidate；保留只读兼容入口，避免审计记录消失。
        for submission in submissions:
            if submission.candidate_id or False:
                continue
            if not self.authorization.can_view_resume_submission(user, submission):
                continue
            items.append(self._item_from_submission(submission, None, user=user))

        return sorted(
            items,
            key=lambda item: (item["updated_at"], item["submission_id"]),
            reverse=True,
        )
    def _item_from_submission(
        self, submission: ResumeSubmission, candidate: Candidate | None, *, user: User
    ) -> dict:
        """把 Candidate、当前简历任务和其附属 Application 投影成一个列表行。"""
        # SourceDocument 缺失本身就是一个必须让用户处理的异常分支。列表不能在这里
        # 404，否则用户连“重新上传”入口都看不到。
        document = self.db.get(SourceDocument, submission.source_document_id)
        source_available = bool(
            document is not None
            and document.document_type == "resume"
            and document.object_ref
        )
        intake = self.intakes.intake_view(submission, user=user)
        candidate_view = intake.get("candidate") or {}
        applications = intake.get("applications") or []
        workflow = intake.get("workflow") or {}
        workflow_run = self.db.get(WorkflowRun, workflow.get("workflow_run_id")) if workflow.get("workflow_run_id") else None
        process = workflow_process_view(self.db, workflow_run)
        bucket, stage = self._status(
            submission,
            document if source_available else None,
            workflow,
            bool(applications),
            candidate,
        )
        candidate_profile = self.db.get(CandidateProfile, candidate.candidate_id) if candidate is not None else None
        return {
            "submission_id": submission.resume_submission_id,
            "candidate_id": candidate.candidate_id if candidate is not None else None,
            "candidate_name": (
                candidate.display_name
                if candidate is not None
                else candidate_view.get("display_name")
                or submission.candidate_name_override
                or "待识别候选人"
            ),
            "candidate_status": candidate.status if candidate is not None else None,
            "candidate_major": candidate_profile.major if candidate_profile is not None else "",
            "resume_profile_id": str(candidate.current_resume_profile_id or "") if candidate is not None else "",
            # 候选人资料是读取型聚合视图。只要当前用户能看到该候选人的任一有效
            # 申请即可打开资料；资料修改仍由写接口执行严格的全申请范围校验。
            "candidate_documents_available": bool(
                candidate is not None
                and self.authorization.can_candidate_material_view(
                    user, candidate.candidate_id
                )
            ),
            "filename": document.original_filename if document is not None else "原始简历文件已缺失",
            "resume_pdf_url": (
                f"/api/v1/resume-documents/{submission.resume_submission_id}/pdf"
                if source_available
                else None
            ),
            "bucket": bucket,
            "stage": stage,
            "submission_status": submission.status,
            "intake_mode": submission.intake_mode,
            "review_kind": submission.review_kind,
            "failure_kind": submission.failure_kind,
            "recovery_code": submission.recovery_code,
            "source_available": source_available,
            "workflow_status": workflow.get("status"),
            "workflow_run_id": workflow.get("workflow_run_id"),
            "process": process.to_public_dict() if process.workflow_run_id else None,
            "applications": applications,
            "application_count": len(applications),
            "routing_status": str(getattr(submission, "routing_status", None) or "idle"),
            "routing_reason": str(getattr(submission, "routing_reason", None) or "") or None,
            "review_reason": intake.get("review_reason")
            or workflow.get("error_message")
            or ("原始简历文件已缺失，请重新上传" if not source_available else None),
            "is_current": bool(intake.get("is_current")),
            "superseded_by_submission_id": intake.get("superseded_by_submission_id"),
            "recovery": dict(intake.get("recovery") or {}),
            "processing_quality": dict(intake.get("processing_quality") or {}),
            "available_actions": intake.get("available_actions") or [],
            "created_at": submission.created_at,
            "updated_at": max(submission.updated_at, candidate.updated_at)
            if candidate is not None
            else submission.updated_at,
        }

    @staticmethod
    def _latest_submissions_by_candidate(
        submissions: list[ResumeSubmission],
    ) -> dict[str, ResumeSubmission]:
        """按更新时间选取每位候选人的当前非 superseded 简历任务。"""
        latest: dict[str, ResumeSubmission] = {}
        for submission in submissions:
            if not submission.candidate_id:
                continue
            if False:
                continue
            latest.setdefault(submission.candidate_id, submission)
        return latest
    def _counts(self, user: User, items: list[dict]) -> dict:
        cursor = self.db.get(NotificationReadCursor, {"user_id": user.user_id, "channel": "candidate_intake"})
        last_read_at = cursor.last_read_at if cursor else datetime.min
        attention = [item for item in items if item["bucket"] in {"review_required", "failed"}]
        return {
            "processing_count": sum(item["bucket"] == "processing" for item in items),
            "attention_count": len(attention),
            "completed_count": sum(item["bucket"] == "completed" for item in items),
            "total_count": len(items),
            "unread_attention_count": sum(item["updated_at"] > last_read_at for item in attention),
        }

    @staticmethod
    def _status(submission: ResumeSubmission, document: SourceDocument | None, workflow: dict, has_applications: bool, candidate: Candidate | None = None) -> tuple[str, str]:
        """简历处理页只以当前 ResumeSubmission 状态判断处理阶段。"""
        if document is None:
            return "failed", "原始简历文件缺失"
        # ResumeSubmission 已完成表示结构化画像已发布。岗位分发无命中、等待
        # 或失败是独立的下游状态，页面通过 routing_status 另行展示，不能把
        # 已完成的简历重新归类成“处理失败/待确认”。
        if submission.status == "completed":
            return "completed", "已创建岗位申请" if has_applications else "简历处理完成"
        routing_status = str(getattr(submission, "routing_status", "") or "")
        if routing_status == "waiting_for_job_profiles":
            return "processing", "等待岗位能力画像生成"
        if routing_status == "processing":
            return "processing", "正在匹配可投递岗位"
        if routing_status == "failed":
            return "failed", "岗位匹配未完成"
        if routing_status == "manual_selection_available":
            return "review_required", "请选择投递岗位"
        if submission.status in {"review_required"}:
            review_kind = str(
                getattr(submission, "review_kind", None)
                or (getattr(submission, "review_context_json", {}) or {}).get("reviewKind")
                or ""
            )
            if review_kind == "structure_metadata":
                return "review_required", "简历基础信息待确认"
            if review_kind in {"structure", "structure_outline"}:
                return "review_required", "简历结构待确认"
            if review_kind.startswith("duplicate") or review_kind == "duplicate":
                return "review_required", "候选人归属待确认"
            return "review_required", "简历重建待确认" if submission.intake_mode != "initial" else "需要人工确认"
        if submission.status == "failed" or workflow.get("status") == "failed":
            return "failed", "简历重建失败" if submission.intake_mode != "initial" else "处理失败"
        if submission.intake_mode != "initial":
            if submission.status == "completed":
                return "completed", "简历已更新"
            return "processing", "正在重新解析简历" if submission.intake_mode == "reparse" else "正在处理新版简历"
        if has_applications:
            return "completed", "已创建岗位申请"
        if submission.status in {"queued", "parsing"}:
            return "processing", "正在解析简历"
        if submission.status in {"extracting"}:
            return "processing", "正在结构化简历"
        return "processing", "正在处理简历"
