"""候选人档案级 HTTP 命令。

与 ``candidate-intakes`` 的 Submission 操作分开：这里的资源是长期存在的
Candidate 聚合，而不是一次 PDF 处理任务。
"""
from __future__ import annotations

import hashlib

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.session import get_db
from backend.app.infrastructure.command_runtime import CommandRunner, CommandSpec
from backend.app.models.entities import Candidate, User
from backend.app.modules.auth.public import get_current_user

from .candidate_deletion_service import CandidateDeletionService


router = APIRouter(prefix="/candidates", tags=["candidates"])


class CandidateDeletionResponse(BaseModel):
    candidate_id: str
    deleted: bool
    already_deleted: bool = False
    application_count: int = 0
    cancelled_workflow_count: int = 0


def _candidate_key(candidate_id: str, user: User, provided: str | None) -> str:
    """为未传请求头的旧客户端生成长度稳定的删除命令幂等键。"""
    if provided:
        return provided
    digest = hashlib.sha256(
        f"candidate.delete:{candidate_id}:{user.user_id}".encode("utf-8")
    ).hexdigest()
    return f"legacy:v1:{digest}"


def _load_candidate(db: Session, candidate_id: str) -> Candidate | None:
    return db.scalar(
        select(Candidate)
        .where(Candidate.candidate_id == candidate_id)
        .with_for_update()
    )


@router.delete("/{candidate_id}", response_model=CandidateDeletionResponse)
def delete_candidate(
    candidate_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """归档一条 Candidate，并在同一事务取消关联进行中任务。"""
    result = CommandRunner(db).execute(
        spec=CommandSpec(
            action="candidate.delete",
            resource_type="candidate",
            permission_code=None,
            requires_system_admin=True,
        ),
        user=user,
        resource_id=candidate_id,
        body={},
        idempotency_key=_candidate_key(candidate_id, user, idempotency_key),
        resource_loader=_load_candidate,
        handler=lambda context: CandidateDeletionService(context.db).archive(
            candidate=context.resource,
            actor=context.user,
        ),
    )
    return CandidateDeletionResponse(
        candidate_id=str(result["candidateId"]),
        deleted=bool(result["deleted"]),
        already_deleted=bool(result.get("alreadyDeleted", False)),
        application_count=int(result.get("applicationCount", 0)),
        cancelled_workflow_count=int(result.get("cancelledWorkflowCount", 0)),
    )
