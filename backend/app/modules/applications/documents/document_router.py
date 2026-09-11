from __future__ import annotations

from backend.app.infrastructure.command_runtime.idempotency_guard import IdempotencyGuard

import hashlib
import json
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Header, Response, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.db.session import get_db
from backend.app.infrastructure.command_runtime import CommandRunner, CommandSpec
from backend.app.models.entities import User
from backend.app.modules.applications.documents.document_schemas import (
    ApplicationDocumentImages,
    ApplicationDocumentPatch,
    ApplicationDocumentsView,
    ApplicationDocumentView,
)
from backend.app.modules.applications.documents.document_service import ApplicationDocumentService
from backend.app.modules.auth.public import get_current_user


router = APIRouter(tags=["application-documents"])


def _command_key(
    provided: str | None, *, user: User, action: str, application_id: str, body: dict
) -> str:
    if provided:
        return provided
    digest = hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return IdempotencyGuard.compatibility_key(provided, user_id=user.user_id, action=action, resource_id=application_id, body=body)


@router.get("/applications/{application_id}/documents", response_model=ApplicationDocumentsView)
def list_application_documents(
    application_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    return ApplicationDocumentService(db).list_view(user, application_id)


@router.post(
    "/applications/{application_id}/documents",
    response_model=ApplicationDocumentView,
    status_code=status.HTTP_201_CREATED,
)
async def upload_application_document(
    application_id: str,
    file: UploadFile = File(...),
    display_name: str | None = Form(default=None, alias="displayName"),
    category: Literal["standard_resume", "psychological_assessment", "academic_transcript", "other"] = Form(default="other"),
    source_stage: Literal["screening", "first_interview", "second_interview", "final_review", "manual_upload"] = Form(default="manual_upload", alias="sourceStage"),
    note: str | None = Form(default=None, max_length=500),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    data = await file.read(settings.document_max_file_size + 1)
    filename = file.filename or "document.pdf"
    body = {
        "display_name": display_name or "",
        "category": category,
        "source_stage": source_stage,
        "note": note or "",
        "filename": filename,
        "content_type": file.content_type or "",
        "sha256": hashlib.sha256(data).hexdigest(),
    }

    def handler(context) -> dict:
        created_object_refs: list[str] = []
        service = ApplicationDocumentService(db)
        response = service.upload(
            user=context.user,
            application_id=application_id,
            display_name=display_name,
            category=category,
            source_stage=source_stage,
            note=note,
            filename=filename,
            content_type=file.content_type,
            data=data,
            created_object_refs=created_object_refs,
        )
        for object_ref in created_object_refs:
            context.compensate_on_rollback(
                lambda object_ref=object_ref: service.store.delete_object(object_ref)
            )
        return response

    return ApplicationDocumentView(
        **CommandRunner(db).execute(
            spec=CommandSpec(
                action="candidate_document.upload",
                authorization_action="upload_candidate_document",
                resource_type="application",
                permission_code="candidate_document.upload",
                audit_exempt=True,
            ),
            user=user,
            resource_id=application_id,
            body=body,
            idempotency_key=_command_key(
                idempotency_key, user=user, action="candidate_document.upload",
                application_id=application_id, body=body,
            ),
            handler=handler,
        )
    )


@router.patch(
    "/applications/{application_id}/documents/{document_id}",
    response_model=ApplicationDocumentView,
)
def update_application_document(
    application_id: str,
    document_id: str,
    body: ApplicationDocumentPatch,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    payload = body.model_dump(mode="json")
    return ApplicationDocumentView(
        **CommandRunner(db).execute(
            spec=CommandSpec(
                action="candidate_document.rename",
                authorization_action="rename_candidate_document",
                resource_type="application",
                permission_code="candidate_document.upload",
                audit_exempt=True,
            ),
            user=user,
            resource_id=application_id,
            body={"document_id": document_id, **payload},
            idempotency_key=_command_key(
                idempotency_key, user=user, action="candidate_document.rename",
                application_id=application_id, body={"document_id": document_id, **payload},
            ),
            handler=lambda context: ApplicationDocumentService(db).update(
                context.user, application_id, document_id, body.displayName
            ),
        )
    )


@router.delete("/applications/{application_id}/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_application_document(
    application_id: str,
    document_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    payload = {"document_id": document_id}
    CommandRunner(db).execute(
        spec=CommandSpec(
            action="candidate_document.delete",
            authorization_action="delete_candidate_document",
            resource_type="application",
            permission_code="candidate_document.upload",
            audit_exempt=True,
        ),
        user=user,
        resource_id=application_id,
        body=payload,
        idempotency_key=_command_key(
            idempotency_key, user=user, action="candidate_document.delete",
            application_id=application_id, body=payload,
        ),
        handler=lambda context: _delete_document(
            db, context.user, application_id, document_id
        ),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _delete_document(db: Session, user: User, application_id: str, document_id: str) -> dict:
    ApplicationDocumentService(db).delete(
        user, application_id, document_id
    )
    return {}


@router.get(
    "/applications/{application_id}/documents/{document_id}/images",
    response_model=ApplicationDocumentImages,
)
def application_document_images(
    application_id: str, document_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    return ApplicationDocumentService(db).image_urls(user, application_id, document_id)


@router.get("/applications/{application_id}/documents/{document_id}/pages/{filename}")
def application_document_page(
    application_id: str, document_id: str, filename: str,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    path = ApplicationDocumentService(db).page_path(
        user, application_id, document_id, filename
    )
    return FileResponse(path, media_type="image/png")
