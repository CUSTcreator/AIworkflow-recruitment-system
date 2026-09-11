from __future__ import annotations

import hashlib
import json
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Header, Response, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.db.session import get_db
from backend.app.infrastructure.command_runtime import CommandRunner, CommandSpec
from backend.app.infrastructure.command_runtime.idempotency_guard import IdempotencyGuard
from backend.app.models.entities import Candidate, User
from backend.app.modules.auth.public import get_current_user
from .candidate_document_schemas import (
    CandidateDocumentImages,
    CandidateDocumentPatch,
    CandidateDocumentsView,
    CandidateDocumentView,
)
from .candidate_document_service import CandidateDocumentService

router = APIRouter(tags=["candidate-documents"])


def _load_candidate(db: Session, candidate_id: str) -> Candidate | None:
    """为 CommandRunner 提供带行锁的 Candidate 聚合资源。"""
    return db.scalar(
        select(Candidate)
        .where(Candidate.candidate_id == candidate_id)
        .with_for_update()
    )


def _key(provided: str | None, user: User, action: str, candidate_id: str, body: dict) -> str:
    if provided:
        return provided
    digest = hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
    return IdempotencyGuard.compatibility_key(None, user_id=user.user_id, action=action, resource_id=candidate_id, body={**body, "digest": digest})


@router.get("/candidates/{candidate_id}/documents", response_model=CandidateDocumentsView)
def list_candidate_documents(candidate_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return CandidateDocumentService(db).list_view(user, candidate_id)


@router.post("/candidates/{candidate_id}/documents", response_model=CandidateDocumentView, status_code=status.HTTP_201_CREATED)
async def upload_candidate_document(candidate_id: str, file: UploadFile = File(...), display_name: str | None = Form(default=None, alias="displayName"), category: Literal["psychological_assessment", "academic_transcript", "certificate", "portfolio", "other"] = Form(default="other"), source_stage: Literal["import", "manual_upload"] = Form(default="manual_upload", alias="sourceStage"), note: str | None = Form(default=None, max_length=500), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    data = await file.read(settings.document_max_file_size + 1)
    filename = file.filename or "document.pdf"
    body = {"display_name": display_name or "", "category": category, "source_stage": source_stage, "filename": filename, "sha256": hashlib.sha256(data).hexdigest()}
    def handler(context):
        refs: list[str] = []
        service = CandidateDocumentService(db)
        result = service.upload(user=context.user, candidate_id=candidate_id, display_name=display_name, category=category, source_stage=source_stage, note=note, filename=filename, content_type=file.content_type, data=data, created_object_refs=refs)
        for ref in refs:
            context.compensate_on_rollback(lambda ref=ref: service.store.delete_object(ref))
        return result
    result = CommandRunner(db).execute(spec=CommandSpec(action="candidate_document.upload", authorization_action="upload_candidate_document", resource_type="candidate", permission_code="candidate_document.upload", audit_exempt=True), user=user, resource_id=candidate_id, body=body, idempotency_key=_key(idempotency_key, user, "candidate_document.upload", candidate_id, body), resource_loader=_load_candidate, handler=handler)
    return CandidateDocumentView(**result)


@router.patch("/candidates/{candidate_id}/documents/{document_id}", response_model=CandidateDocumentView)
def update_candidate_document(candidate_id: str, document_id: str, body: CandidateDocumentPatch, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    payload = {"document_id": document_id, **body.model_dump()}
    result = CommandRunner(db).execute(spec=CommandSpec(action="candidate_document.rename", authorization_action="rename_candidate_document", resource_type="candidate", permission_code="candidate_document.upload", audit_exempt=True), user=user, resource_id=candidate_id, body=payload, idempotency_key=_key(idempotency_key, user, "candidate_document.rename", candidate_id, payload), resource_loader=_load_candidate, handler=lambda context: CandidateDocumentService(db).update(context.user, candidate_id, document_id, body.displayName))
    return CandidateDocumentView(**result)


@router.delete("/candidates/{candidate_id}/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_candidate_document(candidate_id: str, document_id: str, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    payload = {"document_id": document_id}
    CommandRunner(db).execute(spec=CommandSpec(action="candidate_document.delete", authorization_action="delete_candidate_document", resource_type="candidate", permission_code="candidate_document.upload", audit_exempt=True), user=user, resource_id=candidate_id, body=payload, idempotency_key=_key(idempotency_key, user, "candidate_document.delete", candidate_id, payload), resource_loader=_load_candidate, handler=lambda context: (CandidateDocumentService(db).delete(context.user, candidate_id, document_id) or {}))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/candidates/{candidate_id}/documents/{document_id}/images", response_model=CandidateDocumentImages)
def candidate_document_images(candidate_id: str, document_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return CandidateDocumentService(db).image_urls(user, candidate_id, document_id)


@router.get("/candidates/{candidate_id}/documents/{document_id}/pages/{filename}")
def candidate_document_page(candidate_id: str, document_id: str, filename: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return FileResponse(CandidateDocumentService(db).page_path(user, candidate_id, document_id, filename), media_type="image/png")
