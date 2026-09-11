from __future__ import annotations

from backend.app.infrastructure.command_runtime.idempotency_guard import IdempotencyGuard

import hashlib
import json

from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from backend.app.db.session import get_db
from backend.app.infrastructure.command_runtime import CommandRunner, CommandSpec
from backend.app.models.entities import HardScreeningCriterion, User
from backend.app.modules.auth.http.guards import require_business_permission
from backend.app.modules.assessment.hard_screening.hard_screening_catalog_schemas import HardScreeningCriterionCreate, HardScreeningCriterionUpdate
from backend.app.modules.assessment.hard_screening.hard_screening_catalog_service import HardScreeningCatalogService


router = APIRouter(prefix="/admin/hard-screening-criteria", tags=["hard-screening-catalog"])

require_catalog_management = require_business_permission(
    "hard_screening.catalog.manage",
    require_organization_scope=True,
)


def _key(provided: str | None, actor: User, action: str, resource_id: str, body: dict) -> str:
    if provided:
        return provided
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
    return IdempotencyGuard.compatibility_key(provided, user_id=actor.user_id, action=action, resource_id=resource_id, body=body)


def _criterion(db: Session, criterion_id: str):
    return db.query(HardScreeningCriterion).filter(
        HardScreeningCriterion.criterion_id == criterion_id
    ).with_for_update().one_or_none()


@router.get("")
def list_criteria(_: User = Depends(require_catalog_management), db: Session = Depends(get_db)):
    return HardScreeningCatalogService(db).list()


@router.post("")
def create_criterion(
    body: HardScreeningCriterionCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: User = Depends(require_catalog_management), db: Session = Depends(get_db),
):
    payload = body.model_dump(by_alias=False)
    return CommandRunner(db).execute(
        spec=CommandSpec(action="hard_screening.catalog.create", resource_type="system", permission_code="hard_screening.catalog.manage", require_organization_scope=True, audit_exempt=True),
        user=actor, resource_id="hard-screening-catalog", body=payload,
        idempotency_key=_key(idempotency_key, actor, "hard_screening.catalog.create", "hard-screening-catalog", payload),
        handler=lambda context: HardScreeningCatalogService(db).create(context.user, payload),
    )


@router.patch("/{criterion_id}")
def update_criterion(
    criterion_id: str, body: HardScreeningCriterionUpdate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: User = Depends(require_catalog_management), db: Session = Depends(get_db),
):
    payload = body.model_dump(by_alias=False, exclude_unset=True)
    return CommandRunner(db).execute(
        spec=CommandSpec(action="hard_screening.catalog.update", resource_type="system", permission_code="hard_screening.catalog.manage", require_organization_scope=True, audit_exempt=True),
        user=actor, resource_id=criterion_id, body=payload,
        idempotency_key=_key(idempotency_key, actor, "hard_screening.catalog.update", criterion_id, payload),
        resource_loader=_criterion,
        handler=lambda context: HardScreeningCatalogService(db).update(context.user, criterion_id, payload),
    )


@router.delete("/{criterion_id}")
def delete_criterion(
    criterion_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: User = Depends(require_catalog_management), db: Session = Depends(get_db),
):
    payload: dict = {}
    CommandRunner(db).execute(
        spec=CommandSpec(action="hard_screening.catalog.delete", resource_type="system", permission_code="hard_screening.catalog.manage", require_organization_scope=True, audit_exempt=True),
        user=actor, resource_id=criterion_id, body=payload,
        idempotency_key=_key(idempotency_key, actor, "hard_screening.catalog.delete", criterion_id, payload),
        resource_loader=_criterion,
        handler=lambda context: _delete(db, context.user, criterion_id),
    )
    return {"criterionId": criterion_id, "deleted": True}


def _delete(db: Session, actor: User, criterion_id: str) -> dict:
    HardScreeningCatalogService(db).delete(actor, criterion_id)
    return {}


@router.post("/reset")
def reset_criteria(
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: User = Depends(require_catalog_management), db: Session = Depends(get_db),
):
    payload: dict = {}
    return CommandRunner(db).execute(
        spec=CommandSpec(action="hard_screening.catalog.reset", resource_type="system", permission_code="hard_screening.catalog.manage", require_organization_scope=True, audit_exempt=True),
        user=actor, resource_id="hard-screening-catalog", body=payload,
        idempotency_key=_key(idempotency_key, actor, "hard_screening.catalog.reset", "hard-screening-catalog", payload),
        handler=lambda context: HardScreeningCatalogService(db).reset(context.user),
    )
