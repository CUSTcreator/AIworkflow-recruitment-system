"""面试题单 HTTP 适配层：管理模板、绑定和导出。"""

from __future__ import annotations

from backend.app.infrastructure.command_runtime.idempotency_guard import IdempotencyGuard

import hashlib
import json
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.modules.auth.http.dependencies import get_current_user
from backend.app.modules.auth.http.guards import require_business_permission
from backend.app.db.session import get_db
from backend.app.models.entities import (
    Candidate,
    FirstInterviewPlanVersion,
    InterviewGuide,
    InterviewGuideTemplate,
    InterviewGuideTemplateVersion,
    Job,
    User,
)
from backend.app.modules.interview_guides.pdf import build_interview_guide_pdf
from backend.app.modules.interview_guides.schemas import (
    JobTemplateBindingRequest,
    TemplateCreateRequest,
    TemplateDraftUpdateRequest,
    TemplatePublishRequest,
    TemplateStatusRequest,
)
from backend.app.modules.interview_guides.service import InterviewGuideTemplateService
from backend.app.modules.applications.public import ApplicationAccessService
from backend.app.modules.jobs.public import JobAccessService
from backend.app.infrastructure.command_runtime import CommandRunner, CommandSpec


router = APIRouter(tags=["interview-guides"])

require_template_management = require_business_permission(
    "interview_guide.manage",
    require_organization_scope=True,
)


def _key(provided: str | None, user: User, action: str, resource_id: str, body: dict) -> str:
    if provided:
        return provided
    digest = hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return IdempotencyGuard.compatibility_key(provided, user_id=user.user_id, action=action, resource_id=resource_id, body=body)


def _template_management_command(*, db: Session, actor: User, action: str, resource_id: str, body: dict, idempotency_key: str | None, handler, resource_loader=None):
    return CommandRunner(db).execute(
        spec=CommandSpec(action=action, resource_type="system", permission_code="interview_guide.manage", require_organization_scope=True, audit_exempt=True),
        user=actor, resource_id=resource_id, body=body,
        idempotency_key=_key(idempotency_key, actor, action, resource_id, body),
        resource_loader=resource_loader, handler=handler,
    )


def _template_loader(db: Session, template_id: str):
    return db.query(InterviewGuideTemplate).filter(
        InterviewGuideTemplate.template_id == template_id
    ).with_for_update().one_or_none()


def _version_loader(db: Session, version_id: str):
    return db.query(InterviewGuideTemplateVersion).filter(
        InterviewGuideTemplateVersion.template_version_id == version_id
    ).with_for_update().one_or_none()


def _job_loader(db: Session, job_id: str):
    return db.query(Job).filter(Job.job_id == job_id).with_for_update().one_or_none()


@router.get("/admin/interview-guide-templates")
def list_admin_templates(
    _: User = Depends(require_template_management),
    db: Session = Depends(get_db),
):
    return InterviewGuideTemplateService(db).list_templates()


@router.post("/admin/interview-guide-templates")
def create_template(
    body: TemplateCreateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: User = Depends(require_template_management),
    db: Session = Depends(get_db),
):
    payload = body.model_dump(mode="json")
    return _template_management_command(
        db=db, actor=actor, action="interview_guide_template.create", resource_id="templates",
        body=payload, idempotency_key=idempotency_key,
        handler=lambda context: InterviewGuideTemplateService(db).create_draft(
            context.user, name=body.name, template_id=body.templateId,
            questions=[item.model_dump() for item in body.questions], commit=False,
        ),
    )


@router.post("/admin/interview-guide-templates/import")
async def import_template(
    name: str = Form(..., min_length=1, max_length=128),
    template_id: str | None = Form(default=None),
    file: UploadFile = File(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: User = Depends(require_template_management),
    db: Session = Depends(get_db),
):
    data = await file.read()
    filename = file.filename or "template"
    content_type = file.content_type or "application/octet-stream"
    payload = {"name": name, "templateId": template_id or "", "filename": filename, "sha256": hashlib.sha256(data).hexdigest()}

    def handler(context):
        created: list[str] = []
        service = InterviewGuideTemplateService(db)
        result = service.import_draft(
            context.user, name=name, filename=filename, content_type=content_type,
            data=data, template_id=template_id, commit=False, created_object_refs=created,
        )
        for object_ref in created:
            context.compensate_on_rollback(
                lambda object_ref=object_ref: service.store.delete_object(object_ref)
            )
        return result

    return _template_management_command(
        db=db, actor=actor, action="interview_guide_template.import", resource_id="templates",
        body=payload, idempotency_key=idempotency_key, handler=handler,
    )


@router.patch("/admin/interview-guide-template-versions/{template_version_id}")
def update_template_draft(
    template_version_id: str,
    body: TemplateDraftUpdateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: User = Depends(require_template_management),
    db: Session = Depends(get_db),
):
    payload = body.model_dump(exclude_unset=True, mode="json")
    return _template_management_command(
        db=db, actor=actor, action="interview_guide_template.update", resource_id=template_version_id,
        body=payload, idempotency_key=idempotency_key, resource_loader=_version_loader,
        handler=lambda context: InterviewGuideTemplateService(db).update_draft(
            context.user, template_version_id, payload, commit=False
        ),
    )


@router.post("/admin/interview-guide-template-versions/{template_version_id}/publish")
def publish_template(
    template_version_id: str,
    body: TemplatePublishRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: User = Depends(require_template_management),
    db: Session = Depends(get_db),
):
    payload = body.model_dump(mode="json")
    return _template_management_command(
        db=db, actor=actor, action="interview_guide_template.publish", resource_id=template_version_id,
        body=payload, idempotency_key=idempotency_key, resource_loader=_version_loader,
        handler=lambda context: InterviewGuideTemplateService(db).publish(
            context.user, template_version_id, is_default=body.isDefault, commit=False
        ),
    )


@router.post("/admin/interview-guide-templates/{template_id}/archive")
def archive_template(
    template_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: User = Depends(require_template_management),
    db: Session = Depends(get_db),
):
    return _template_management_command(
        db=db, actor=actor, action="interview_guide_template.archive", resource_id=template_id,
        body={}, idempotency_key=idempotency_key, resource_loader=_template_loader,
        handler=lambda context: InterviewGuideTemplateService(db).archive(
            context.user, template_id, commit=False
        ),
    )


@router.patch("/admin/interview-guide-templates/{template_id}/status")
def update_template_status(
    template_id: str,
    body: TemplateStatusRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: User = Depends(require_template_management),
    db: Session = Depends(get_db),
):
    payload = body.model_dump(mode="json")
    return _template_management_command(
        db=db, actor=actor, action="interview_guide_template.status", resource_id=template_id,
        body=payload, idempotency_key=idempotency_key, resource_loader=_template_loader,
        handler=lambda context: InterviewGuideTemplateService(db).set_active(
            context.user, template_id, is_active=body.isActive, commit=False
        ),
    )


@router.get("/interview-guide-templates/published")
def list_published_templates(
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return InterviewGuideTemplateService(db).published_templates()


@router.get("/job-management/jobs/{job_id}/interview-guide-template")
def get_job_template_binding(
    job_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="岗位不存在")
    JobAccessService(db).assert_visible(user, job)
    return InterviewGuideTemplateService(db).job_binding_view(job)


@router.patch("/job-management/jobs/{job_id}/interview-guide-template")
def update_job_template_binding(
    job_id: str,
    body: JobTemplateBindingRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    payload = body.model_dump(mode="json")
    return CommandRunner(db).execute(
        spec=CommandSpec(action="interview_guide_template.bind_job", resource_type="job", permission_code="job.edit", audit_exempt=True),
        user=actor, resource_id=job_id, body=payload,
        idempotency_key=_key(idempotency_key, actor, "interview_guide_template.bind_job", job_id, payload),
        resource_loader=_job_loader,
        handler=lambda context: InterviewGuideTemplateService(db).bind_job(
            context.user, job_id, body.templateId, commit=False
        ),
    )


@router.get("/applications/{application_id}/interviews/first/guide/export.pdf")
def export_first_interview_guide(
    application_id: str,
    draft: bool = False,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    application = ApplicationAccessService(db).get_visible(user, application_id)
    candidate = db.get(Candidate, application.candidate_id)
    job = db.get(Job, application.job_id)
    guide: dict | None = None
    is_draft_export = False

    if draft:
        # 确认页的草稿保存在规划版本展示区，而不是执行态 InterviewGuide。
        # InterviewGuide 只有点击“确认题单”后才会创建，不能用它判断草稿是否存在。
        plan = db.scalars(
            select(FirstInterviewPlanVersion)
            .where(FirstInterviewPlanVersion.application_id == application_id)
            .order_by(
                FirstInterviewPlanVersion.version.desc(),
                FirstInterviewPlanVersion.created_at.desc(),
            )
        ).first()
        presentation = (
            plan.presentation_json
            if plan is not None and isinstance(plan.presentation_json, dict)
            else {}
        )
        raw_draft_guide = presentation.get("draft_guide")
        draft_guide = dict(raw_draft_guide) if isinstance(raw_draft_guide, dict) else {}
        if draft_guide:
            guide = InterviewGuideTemplateService(db).attach_preview(draft_guide, job)
            is_draft_export = True

    else:
        rows = list(db.scalars(
            select(InterviewGuide)
            .where(InterviewGuide.application_id == application_id)
            .order_by(InterviewGuide.created_at.desc(), InterviewGuide.id.desc())
        ))
        guide = next(
            (
                row.content_json
                for row in rows
                if (row.content_json or {}).get("guideStatus") == "confirmed"
            ),
            None,
        )

    if is_draft_export and guide is not None:
        guide = {
            **guide,
            "questions": list(guide.get("technicalQuestions") or []) + list(guide.get("commonQuestions") or []),
            "sections": [
                {"sectionType": "technical", "questions": guide.get("technicalQuestions") or []},
                {"sectionType": "common", "questions": guide.get("commonQuestions") or []},
            ],
        }
    if guide is None or candidate is None or job is None:
        message = "当前没有可预览的一面题单草稿" if draft else "正式题单尚未生成"
        raise HTTPException(status_code=409, detail=message)
    data = build_interview_guide_pdf(
        guide={**guide, "isDraft": is_draft_export},
        candidate_name=candidate.display_name,
        job_title=job.title,
    )
    filename = f"{job.title}_{candidate.display_name}_一面题单.pdf"
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )
