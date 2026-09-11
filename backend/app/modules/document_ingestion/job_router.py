from __future__ import annotations

from backend.app.infrastructure.command_runtime.idempotency_guard import IdempotencyGuard

import hashlib
import json

from fastapi import APIRouter, Depends, File, Header, UploadFile, status
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.db.session import get_db
from backend.app.infrastructure.command_runtime import CommandRunner, CommandSpec
from backend.app.models.entities import SourceDocument, User
from backend.app.modules.auth.public import get_current_user
from backend.app.modules.document_ingestion.schemas.command_schemas import (
    ConfirmJobDraftsRequest,
    JobDocumentRepairRequest,
    JobDraftPatch,
)
from backend.app.modules.document_ingestion.schemas.view_schemas import (
    ConfirmJobDraftsResponse,
    DeleteJobDraftResponse,
    JobDocumentUploadResponse,
    JobDraftView,
    JobDocumentImportView,
)
from backend.app.modules.document_ingestion.services import JobDocumentService
from backend.app.modules.jobs.job_recovery import job_recovery_plan, job_recovery_actions


router = APIRouter(prefix="/job-documents", tags=["job-documents"])


def _load_job_document(db: Session, document_id: str) -> SourceDocument | None:
    return (
        db.query(SourceDocument)
        .filter(
            SourceDocument.source_document_id == document_id,
            SourceDocument.document_type == "job_requirement",
        )
        .with_for_update()
        .one_or_none()
    )


def _command_key(
    provided: str | None,
    *,
    user: User,
    action: str,
    resource_id: str,
    body: dict,
) -> str:
    """兼容未升级客户端；后备键仍由用户、动作、资源和请求体稳定派生。"""
    if provided:
        return provided
    fingerprint = hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:32]
    return IdempotencyGuard.compatibility_key(provided, user_id=user.user_id, action=action, resource_id=resource_id, body=body)


@router.post("", response_model=JobDocumentUploadResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_job_document(
    file: UploadFile = File(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    data = await file.read(settings.document_max_file_size + 1)
    filename = file.filename or "job-requirements.xlsx"
    content_type = file.content_type
    source_sha256 = hashlib.sha256(data).hexdigest()

    def handler(context) -> dict:
        service = JobDocumentService(db)
        document, import_task, run, reused = service.uploads.upload_job_document(
            user=context.user,
            filename=filename,
            content_type=content_type,
            data=data,
           
        )
        if not reused:
            context.compensate_on_rollback(
                lambda object_ref=document.object_ref: service.uploads.store.delete_object(object_ref)
            )
        return {
            "document_id": document.source_document_id,
            "import_id": import_task.job_document_import_id,
            "workflow_run_id": run.workflow_run_id,
            "import_status": import_task.status,
            "reused": reused,
        }

    return JobDocumentUploadResponse(
        **CommandRunner(db).execute(
            spec=CommandSpec(
                action="job_document.upload",
                resource_type="upload",
                permission_code="job_document.upload",
                require_organization_scope=True,
                # 未升级客户端交给内容哈希去重；显式 Key 才进入账本重放。
                idempotent=bool(idempotency_key),
            ),
            user=user,
            resource_id=source_sha256,
            body={"filename": filename, "content_type": content_type or "", "sha256": source_sha256},
            idempotency_key=_command_key(idempotency_key, user=user, action="job_document.upload", resource_id=source_sha256, body={"sha256": source_sha256, "filename": filename}),
            handler=handler,
        )
    )


@router.get("/{document_id}", response_model=JobDocumentImportView)
def get_job_document(
    document_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = JobDocumentService(db)
    document = service.get_document(document_id, user)
    import_task = service.get_import(document_id, user)
    plan = job_recovery_plan(
        import_task.recovery_code,
        has_extraction=bool(document.parsed_text or document.document_blocks_ref),
    ) if import_task.recovery_code else None
    return {
        "import_id": import_task.job_document_import_id,
        "source_document_id": document.source_document_id,
        "document_type": document.document_type,
        "original_filename": document.original_filename,
        "import_status": import_task.status,
        "error_message": import_task.error_message,
        "recovery_code": import_task.recovery_code,
        "recovery_context_json": import_task.recovery_context_json,
        "available_actions": job_recovery_actions(list(plan.actions)) if plan else [],
        "parser_provider": document.parser_provider,
        "document_blocks_ref": document.document_blocks_ref,
        "document_blocks_sha256": document.document_blocks_sha256,
        "document_blocks_schema_version": document.document_blocks_schema_version,
        "created_at": import_task.created_at,
        "updated_at": import_task.updated_at,
    }


@router.get("/{document_id}/drafts", response_model=list[JobDraftView])
def list_job_drafts(
    document_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return JobDocumentService(db).list_drafts(document_id, user)


@router.patch("/{document_id}/drafts/{draft_id}", response_model=JobDraftView)
def update_job_draft(
    document_id: str,
    draft_id: str,
    body: JobDraftPatch,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    payload = body.model_dump(exclude_unset=True)
    return CommandRunner(db).execute(
        spec=CommandSpec(
            action="job_document.draft.update",
            resource_type="source_document",
            permission_code="job_document.upload",
            require_organization_scope=True,
        ),
        user=user,
        resource_id=document_id,
        body={"draft_id": draft_id, **payload},
        idempotency_key=_command_key(idempotency_key, user=user, action="job_document.draft.update", resource_id=document_id, body={"draft_id": draft_id, **payload}),
        resource_loader=_load_job_document,
        handler=lambda context: JobDocumentService(db).update_draft(
            document_id, draft_id, context.user, payload
        ),
    )


@router.delete("/{document_id}/drafts/{draft_id}", response_model=DeleteJobDraftResponse)
def delete_job_draft(
    document_id: str,
    draft_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return CommandRunner(db).execute(
        spec=CommandSpec(
            action="job_document.draft.delete",
            resource_type="source_document",
            permission_code="job_document.confirm",
            require_organization_scope=True,
        ),
        user=user,
        resource_id=document_id,
        body={"draft_id": draft_id},
        idempotency_key=_command_key(idempotency_key, user=user, action="job_document.draft.delete", resource_id=document_id, body={"draft_id": draft_id}),
        resource_loader=_load_job_document,
        handler=lambda context: JobDocumentService(db).delete_draft(
            document_id, draft_id, context.user
        ),
    )


@router.post("/{document_id}/confirm", response_model=ConfirmJobDraftsResponse)
def confirm_job_drafts(
    document_id: str,
    body: ConfirmJobDraftsRequest | None = None,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    confirmations = body.drafts if body else None
    draft_ids = (
        [item.draft_id for item in confirmations]
        if confirmations is not None
        else body.draft_ids if body else None
    )
    confirmed_rules = (
        {
            item.draft_id: [rule.model_dump(mode="json") for rule in item.hard_screening_rules]
            for item in confirmations
        }
        if confirmations is not None
        else None
    )

    def handler(context) -> dict:
        service = JobDocumentService(db)
        jobs = service.confirm_drafts(
            document_id,
            context.user,
            draft_ids,
            confirmed_hard_screening_rules=confirmed_rules,
        )
        import_task = service.get_import(document_id, context.user)
        return {
            "document_id": document_id,
            "import_status": import_task.status,
            "jobs": jobs,
        }

    return ConfirmJobDraftsResponse(
        **CommandRunner(db).execute(
            spec=CommandSpec(
                action="job_document.confirm",
                resource_type="source_document",
                permission_code="job_document.confirm",
                require_organization_scope=True,
            ),
            user=user,
            resource_id=document_id,
            body={"draft_ids": draft_ids or []},
            idempotency_key=_command_key(idempotency_key, user=user, action="job_document.confirm", resource_id=document_id, body={"draft_ids": draft_ids or []}),
            resource_loader=_load_job_document,
            handler=handler,
        )
    )


@router.post("/{document_id}/retry", response_model=JobDocumentUploadResponse, status_code=status.HTTP_202_ACCEPTED)
def retry_job_document(
    document_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    def handler(context) -> dict:
        service = JobDocumentService(db)
        run = service.uploads.retry(context.resource, context.user)
        import_task = service.get_import(context.resource.source_document_id, context.user)
        return {
            "document_id": context.resource.source_document_id,
            "import_id": import_task.job_document_import_id,
            "workflow_run_id": run.workflow_run_id,
            "import_status": import_task.status,
            "reused": False,
        }

    return JobDocumentUploadResponse(
        **CommandRunner(db).execute(
            spec=CommandSpec(
                action="job_document.retry",
                resource_type="source_document",
                permission_code="job_document.upload",
                require_organization_scope=True,
            ),
            user=user,
            resource_id=document_id,
            body={},
            idempotency_key=_command_key(idempotency_key, user=user, action="job_document.retry", resource_id=document_id, body={}),
            resource_loader=_load_job_document,
            handler=handler,
        )
    )


@router.post("/{document_id}/repair", response_model=JobDocumentUploadResponse, status_code=status.HTTP_202_ACCEPTED)
def repair_job_document(
    document_id: str,
    body: JobDocumentRepairRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """提交表头/字段人工修复，并从岗位提取 Step 继续运行。"""
    payload = body.model_dump(exclude_none=True)

    def handler(context) -> dict:
        service = JobDocumentService(db)
        document = service.get_document(context.resource.source_document_id, context.user)
        run = service.uploads.submit_repair(
            document,
            context.user,
            action=body.action,
            sheet_name=body.sheet_name,
            header_row_index=body.header_row_index,
            header_mapping=body.header_mapping,
            fields=body.fields,
        )
        import_task = service.get_import(document.source_document_id, context.user)
        return {
            "document_id": document.source_document_id,
            "import_id": import_task.job_document_import_id,
            "workflow_run_id": run.workflow_run_id,
            "import_status": import_task.status,
            "reused": False,
        }

    return JobDocumentUploadResponse(
        **CommandRunner(db).execute(
            spec=CommandSpec(
                action="job_document.repair",
                resource_type="source_document",
                permission_code="job_document.upload",
                require_organization_scope=True,
            ),
            user=user,
            resource_id=document_id,
            body=payload,
            idempotency_key=_command_key(idempotency_key, user=user, action="job_document.repair", resource_id=document_id, body=payload),
            resource_loader=_load_job_document,
            handler=handler,
        )
    )
