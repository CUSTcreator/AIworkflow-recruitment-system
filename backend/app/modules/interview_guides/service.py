from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.app.shared.errors import BusinessRuleError
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from backend.app.models.entities import (
    InterviewGuideTemplate,
    InterviewGuideTemplateVersion,
    Job,
    SourceDocument,
    User,
)
from backend.app.shared.audit import record_audit_event
from backend.app.modules.auth.public import AuthorizationService
from backend.app.storage.object_store import ObjectStore
from backend.app.modules.interview_guides.parser import parse_template_file


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12].upper()}"


class InterviewGuideTemplateService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.store = ObjectStore()

    def list_templates(
        self, *, include_inactive: bool = True, include_archived: bool = True
    ) -> list[dict[str, Any]]:
        query = select(InterviewGuideTemplate).order_by(
            InterviewGuideTemplate.archived_at.is_not(None),
            InterviewGuideTemplate.is_default.desc(),
            InterviewGuideTemplate.updated_at.desc(),
        )
        if not include_inactive:
            query = query.where(InterviewGuideTemplate.is_active.is_(True))
        if not include_archived:
            query = query.where(InterviewGuideTemplate.archived_at.is_(None))
        return [self._template_view(item) for item in self.db.scalars(query)]

    def published_templates(self) -> list[dict[str, Any]]:
        return [
            item for item in self.list_templates(include_inactive=False, include_archived=False)
            if item.get("publishedVersion") is not None
        ]

    def create_draft(
        self,
        actor: User,
        *,
        name: str,
        questions: list[dict[str, Any]],
        template_id: str | None = None,
        source_document_id: str | None = None,
        commit: bool = True,
    ) -> dict[str, Any]:
        clean_name = name.strip()
        if template_id:
            template = self.db.get(InterviewGuideTemplate, template_id)
            if template is None:
                raise BusinessRuleError(status_code=404, detail="通用题单不存在")
            if template.archived_at is not None:
                raise BusinessRuleError(status_code=409, detail="已归档题单不能创建新版本")
            template.name = clean_name
            template.updated_at = datetime.now(UTC).replace(tzinfo=None)
        else:
            template = InterviewGuideTemplate(
                template_id=_id("GUIDE_TEMPLATE"),
                name=clean_name,
                round="first",
                is_default=False,
                is_active=True,
                created_by=actor.user_id,
                created_at=datetime.now(UTC).replace(tzinfo=None),
                updated_at=datetime.now(UTC).replace(tzinfo=None),
            )
            self.db.add(template)
            self.db.flush()
        version = int(self.db.scalar(
            select(func.max(InterviewGuideTemplateVersion.version)).where(
                InterviewGuideTemplateVersion.template_id == template.template_id
            )
        ) or 0) + 1
        normalized = self._normalize_questions(questions, template.template_id, version)
        if not normalized:
            raise BusinessRuleError(status_code=422, detail="题单至少包含一道有效题目")
        row = InterviewGuideTemplateVersion(
            template_version_id=_id("GUIDE_VERSION"),
            template_id=template.template_id,
            version=version,
            status="draft",
            source_document_id=source_document_id,
            questions_json=normalized,
            created_by=actor.user_id,
            created_at=datetime.now(UTC).replace(tzinfo=None),
            updated_at=datetime.now(UTC).replace(tzinfo=None),
        )
        self.db.add(row)
        record_audit_event(
            self.db,
            actor=actor,
            action="interview_guide_template.create",
            target_type="interview_guide_template",
            target_id=template.template_id,
            summary=f"创建通用题单草稿：{template.name} V{version}",
        )
        self._finish(commit)
        return self._template_view(template)

    def import_draft(
        self,
        actor: User,
        *,
        name: str,
        filename: str,
        content_type: str,
        data: bytes,
        template_id: str | None,
        commit: bool = True,
        created_object_refs: list[str] | None = None,
    ) -> dict[str, Any]:
        if not data:
            raise BusinessRuleError(status_code=422, detail="上传文件为空")
        if len(data) > 10 * 1024 * 1024:
            raise BusinessRuleError(status_code=413, detail="题单文件不能超过10MB")
        try:
            questions = parse_template_file(filename, data)
        except ValueError as exc:
            raise BusinessRuleError(status_code=422, detail=str(exc)) from exc
        if not questions:
            raise BusinessRuleError(status_code=422, detail="没有从文件中识别到题目，请检查文件内容")
        digest = hashlib.sha256(data).hexdigest()
        document = self.db.scalar(
            select(SourceDocument).where(
                SourceDocument.document_type == "interview_guide_template",
                SourceDocument.source_sha256 == digest,
            )
        )
        if document is None:
            object_ref, _ = self.store.put_bytes(
                f"interview-guide-templates/{digest}/{Path(filename).name}",
                data,
                content_type or "application/octet-stream",
            )
            if created_object_refs is not None:
                created_object_refs.append(object_ref)
            document = SourceDocument(
                source_document_id=_id("DOC"),
                document_type="interview_guide_template",
                original_filename=Path(filename).name,
                content_type=content_type or "application/octet-stream",
                object_ref=object_ref,
                source_sha256=digest,
                parsed_text="\n".join(item["question"] for item in questions),
                uploaded_by=actor.user_id,
                created_at=datetime.now(UTC).replace(tzinfo=None),
                updated_at=datetime.now(UTC).replace(tzinfo=None),
            )
            self.db.add(document)
            self.db.flush()
        return self.create_draft(
            actor,
            name=name,
            questions=questions,
            template_id=template_id,
            source_document_id=document.source_document_id,
            commit=commit,
        )

    def update_draft(
        self,
        actor: User,
        template_version_id: str,
        changes: dict[str, Any],
        commit: bool = True,
    ) -> dict[str, Any]:
        version = self.db.get(InterviewGuideTemplateVersion, template_version_id)
        if version is None:
            raise BusinessRuleError(status_code=404, detail="题单版本不存在")
        if version.status != "draft":
            raise BusinessRuleError(status_code=409, detail="已发布版本不能直接修改，请创建新版本")
        template = self.db.get(InterviewGuideTemplate, version.template_id)
        if template is None:
            raise BusinessRuleError(status_code=404, detail="通用题单不存在")
        if template.archived_at is not None:
            raise BusinessRuleError(status_code=409, detail="已归档题单不能修改")
        if changes.get("name") is not None:
            template.name = str(changes["name"]).strip()
        if changes.get("questions") is not None:
            normalized = self._normalize_questions(changes["questions"], template.template_id, version.version)
            if not normalized:
                raise BusinessRuleError(status_code=422, detail="题单至少包含一道有效题目")
            version.questions_json = normalized
        now = datetime.now(UTC).replace(tzinfo=None)
        template.updated_at = now
        version.updated_at = now
        record_audit_event(
            self.db,
            actor=actor,
            action="interview_guide_template.update",
            target_type="interview_guide_template",
            target_id=template.template_id,
            summary=f"修改通用题单草稿：{template.name} V{version.version}",
        )
        self._finish(commit)
        return self._template_view(template)

    def publish(self, actor: User, template_version_id: str, *, is_default: bool,
        commit: bool = True,
    ) -> dict[str, Any]:
        version = self.db.get(InterviewGuideTemplateVersion, template_version_id)
        if version is None:
            raise BusinessRuleError(status_code=404, detail="题单版本不存在")
        template = self.db.get(InterviewGuideTemplate, version.template_id)
        if template is None:
            raise BusinessRuleError(status_code=404, detail="通用题单不存在")
        if template.archived_at is not None:
            raise BusinessRuleError(status_code=409, detail="已归档题单不能发布")
        if not list(version.questions_json or []):
            raise BusinessRuleError(status_code=422, detail="空题单不能发布")
        self.db.execute(
            update(InterviewGuideTemplateVersion)
            .where(
                InterviewGuideTemplateVersion.template_id == template.template_id,
                InterviewGuideTemplateVersion.status == "published",
                InterviewGuideTemplateVersion.template_version_id != version.template_version_id,
            )
            .values(status="retired", updated_at=datetime.now(UTC).replace(tzinfo=None))
        )
        if is_default:
            self.db.execute(
                update(InterviewGuideTemplate)
                .where(
                    InterviewGuideTemplate.round == template.round,
                    InterviewGuideTemplate.template_id != template.template_id,
                )
                .values(is_default=False)
            )
        template.is_default = is_default or template.is_default
        template.is_active = True
        template.updated_at = datetime.now(UTC).replace(tzinfo=None)
        version.status = "published"
        version.published_at = datetime.now(UTC).replace(tzinfo=None)
        version.updated_at = datetime.now(UTC).replace(tzinfo=None)
        record_audit_event(
            self.db,
            actor=actor,
            action="interview_guide_template.publish",
            target_type="interview_guide_template",
            target_id=template.template_id,
            summary=f"发布通用题单：{template.name} V{version.version}",
        )
        self._finish(commit)
        return self._template_view(template)

    def set_active(self, actor: User, template_id: str, *, is_active: bool,
        commit: bool = True,
    ) -> dict[str, Any]:
        template = self.db.get(InterviewGuideTemplate, template_id)
        if template is None:
            raise BusinessRuleError(status_code=404, detail="通用题单不存在")
        if template.archived_at is not None:
            raise BusinessRuleError(status_code=409, detail="已归档题单不能调整启用状态")
        if not is_active and template.is_default:
            raise BusinessRuleError(status_code=409, detail="默认通用题单不能直接停用，请先设置其他默认题单")
        template.is_active = is_active
        template.updated_at = datetime.now(UTC).replace(tzinfo=None)
        record_audit_event(
            self.db,
            actor=actor,
            action="interview_guide_template.status",
            target_type="interview_guide_template",
            target_id=template.template_id,
            summary=f"{'启用' if is_active else '停用'}通用题单：{template.name}",
        )
        self._finish(commit)
        return self._template_view(template)

    def archive(self, actor: User, template_id: str,
        commit: bool = True,
    ) -> dict[str, Any]:
        """归档模板而不删除版本，已冻结到历史申请中的题目不会受影响。"""
        template = self.db.get(InterviewGuideTemplate, template_id)
        if template is None:
            raise BusinessRuleError(status_code=404, detail="通用题单不存在")
        if template.archived_at is not None:
            raise BusinessRuleError(status_code=409, detail="题单已归档")
        if template.is_default:
            raise BusinessRuleError(status_code=409, detail="默认通用题单不能归档，请先设置其他默认题单")
        bound_job = self.db.scalar(
            select(Job.title).where(
                Job.common_interview_template_id == template.template_id,
                Job.deleted_at.is_(None),
            ).limit(1)
        )
        if bound_job:
            raise BusinessRuleError(status_code=409, detail=f"题单仍被岗位“{bound_job}”绑定，请先解除或替换绑定")
        now = datetime.now(UTC).replace(tzinfo=None)
        template.is_active = False
        template.archived_at = now
        template.archived_by_user_id = actor.user_id
        template.updated_at = now
        record_audit_event(
            self.db, actor=actor, action="interview_guide_template.archive",
            target_type="interview_guide_template", target_id=template.template_id,
            summary=f"归档通用题单：{template.name}",
        )
        self._finish(commit)
        return self._template_view(template)

    def bind_job(self, actor: User, job_id: str, template_id: str | None,
        commit: bool = True,
    ) -> dict[str, Any]:
        job = self.db.get(Job, job_id)
        if job is None:
            raise BusinessRuleError(status_code=404, detail="岗位不存在")
        AuthorizationService(self.db).require_business_action(
            actor, "job.edit", department_id=job.department_id
        )
        if template_id:
            template = self.db.get(InterviewGuideTemplate, template_id)
            if template is None:
                raise BusinessRuleError(status_code=404, detail="通用题单不存在")
            if template.archived_at is not None:
                raise BusinessRuleError(status_code=409, detail="已归档题单不能绑定岗位")
            if not template.is_active or self._published_version(template.template_id) is None:
                raise BusinessRuleError(status_code=409, detail="只能绑定已发布且启用的通用题单")
        job.common_interview_template_id = template_id or None
        record_audit_event(
            self.db,
            actor=actor,
            action="interview_guide_template.bind_job",
            target_type="job",
            target_id=job.job_id,
            summary=f"更新岗位通用题单：{job.title}",
            details={"templateId": template_id},
        )
        self._finish(commit)
        return self.job_binding_view(job)

    def job_binding_view(self, job: Job) -> dict[str, Any]:
        resolved = self.resolve_for_job(job)
        return {
            "jobId": job.job_id,
            "configuredTemplateId": job.common_interview_template_id,
            "inheritDefault": job.common_interview_template_id is None,
            "resolvedTemplate": resolved,
        }

    def resolve_for_job(self, job: Job) -> dict[str, Any] | None:
        template = self.db.get(InterviewGuideTemplate, job.common_interview_template_id) if job.common_interview_template_id else None
        if template is None:
            template = self.db.scalar(
                select(InterviewGuideTemplate).where(
                    InterviewGuideTemplate.round == "first",
                    InterviewGuideTemplate.is_default.is_(True),
                    InterviewGuideTemplate.is_active.is_(True),
                )
            )
        if template is None or template.archived_at is not None or not template.is_active:
            return None
        version = self._published_version(template.template_id)
        if version is None:
            return None
        return {
            "templateId": template.template_id,
            "templateVersionId": version.template_version_id,
            "name": template.name,
            "version": version.version,
            "isDefault": template.is_default,
            "questions": list(version.questions_json or []),
        }

    def attach_preview(self, plan: dict[str, Any], job: Job) -> dict[str, Any]:
        if plan.get("guideStatus") == "confirmed" or plan.get("confirmed"):
            return plan
        technical = list(plan.get("technicalQuestions") or plan.get("questions") or [])
        technical = [{**item, "sectionType": "technical"} for item in technical]
        resolved = self.resolve_for_job(job)
        common = self._common_interview_questions(resolved) if resolved else []
        return {
            **plan,
            "questions": technical,
            "technicalQuestions": technical,
            "commonQuestions": common,
            "commonTemplate": resolved,
            "commonTemplateStatus": "ready" if resolved else "missing",
        }

    def combine_confirmed(self, plan: dict[str, Any], job: Job) -> dict[str, Any]:
        resolved = self.resolve_for_job(job)
        technical_source = plan.get("technicalQuestions") or [
            item for item in (plan.get("questions") or []) if item.get("sectionType") != "common"
        ]
        # 新版确认页默认把 AI 建议题放在 questionSuggestions；确认动作选择该题单时，
        # 它们必须与人工加入的 questions 一样被物化为正式逐题记录。
        if not technical_source:
            technical_source = plan.get("questionSuggestions") or []
        technical = [
            {**item, "sectionType": "technical", "confirmed": True}
            for item in technical_source
            if isinstance(item, dict)
        ]
        common = self._common_interview_questions(resolved, confirmed=True) if resolved else []
        return {
            **plan,
            "questions": technical + common,
            "technicalQuestions": technical,
            "commonQuestions": common,
            "sections": [
                {"sectionType": "technical", "title": "技术题单", "questions": technical},
                {"sectionType": "common", "title": "通用题单", "questions": common},
            ],
            "commonTemplate": {key: value for key, value in resolved.items() if key != "questions"} if resolved else None,
            "commonTemplateVersion": resolved["version"] if resolved else None,
            "commonTemplateVersionId": resolved["templateVersionId"] if resolved else None,
            "commonTemplateStatus": "ready" if resolved else "missing",
            "questionCount": len(technical) + len(common),
        }

    def _finish(self, commit: bool) -> None:
        # 保留参数仅兼容旧内部调用；提交权只属于外层 CommandRunner。
        self.db.flush()

    def _template_view(self, template: InterviewGuideTemplate) -> dict[str, Any]:
        versions = list(self.db.scalars(
            select(InterviewGuideTemplateVersion)
            .where(InterviewGuideTemplateVersion.template_id == template.template_id)
            .order_by(InterviewGuideTemplateVersion.version.desc())
        ))
        latest = versions[0] if versions else None
        published = next((item for item in versions if item.status == "published"), None)
        return {
            "templateId": template.template_id,
            "name": template.name,
            "round": template.round,
            "isDefault": template.is_default,
            "isActive": template.is_active,
            "archivedAt": template.archived_at.isoformat() if template.archived_at else None,
            "latestVersion": latest.version if latest else None,
            "latestVersionId": latest.template_version_id if latest else None,
            "latestStatus": latest.status if latest else None,
            "publishedVersion": published.version if published else None,
            "publishedVersionId": published.template_version_id if published else None,
            "questions": list(latest.questions_json or []) if latest else [],
            "sourceDocumentId": latest.source_document_id if latest else None,
            "versions": [
                {
                    "templateVersionId": item.template_version_id,
                    "version": item.version,
                    "status": item.status,
                    "questionCount": len(item.questions_json or []),
                    "sourceDocumentId": item.source_document_id,
                    "createdAt": item.created_at.isoformat(),
                    "publishedAt": item.published_at.isoformat() if item.published_at else None,
                }
                for item in versions
            ],
            "updatedAt": template.updated_at.isoformat(),
        }

    def _published_version(self, template_id: str) -> InterviewGuideTemplateVersion | None:
        return self.db.scalar(
            select(InterviewGuideTemplateVersion)
            .where(
                InterviewGuideTemplateVersion.template_id == template_id,
                InterviewGuideTemplateVersion.status == "published",
            )
            .order_by(InterviewGuideTemplateVersion.version.desc())
        )

    @staticmethod
    def _normalize_questions(questions: list[dict[str, Any]], template_id: str, version: int) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for index, item in enumerate(questions, start=1):
            question = str(item.get("question") or item.get("mainQuestion") or "").strip()
            if not question:
                continue
            normalized.append({
                "questionId": str(item.get("questionId") or f"GQ_{template_id[-8:]}_{version}_{index:03d}"),
                "question": question,
                "evaluationPoints": [str(value).strip() for value in (item.get("evaluationPoints") or []) if str(value).strip()],
                "required": bool(item.get("required", True)),
                "resultType": "non_scoring" if item.get("resultType") == "non_scoring" else "capability",
                "order": index,
            })
        return normalized

    @staticmethod
    def _common_interview_questions(resolved: dict[str, Any], *, confirmed: bool = False) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for item in resolved.get("questions") or []:
            result.append({
                "questionId": item["questionId"],
                "verificationTargetIds": [],
                "mainQuestion": item["question"],
                "finalText": item["question"],
                "followUpQuestions": [],
                "expectedEvidence": list(item.get("evaluationPoints") or []),
                "evaluationPoints": list(item.get("evaluationPoints") or []),
                "negativeSignals": [],
                "priority": "medium",
                "confirmed": confirmed,
                "sourceType": "common_template",
                "sectionType": "common",
                "resultType": item.get("resultType") or "capability",
                "required": bool(item.get("required", True)),
                "templateVersionId": resolved["templateVersionId"],
            })
        return result
