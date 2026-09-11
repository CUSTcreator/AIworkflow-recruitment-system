"""Workflow 阶段产物的事务外暂存与事务内发布。"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.entities import WorkflowArtifact, WorkflowRun
# ObjectStore 反向依赖 ExternalActivity；此处必须在真正读写对象时局部导入，
# 否则 ObjectStore -> workflow_runtime.__init__ -> ActivityRunner -> ArtifactStore 会形成循环导入。
# WorkflowArtifactStore 仍是唯一负责工作流工件读写编排的组件。


_publishing_artifact: ContextVar[bool] = ContextVar(
    "recruit_workflow_artifact_publish_phase", default=False
)


@contextmanager
def artifact_publish_scope():
    """标记持久化短事务；该期间禁止通过 Artifact 读取对象存储。"""
    token = _publishing_artifact.set(True)
    try:
        yield
    finally:
        _publishing_artifact.reset(token)


@dataclass(frozen=True, slots=True)
class StagedJsonArtifact:
    """已在 handler 阶段准备好的 JSON Artifact。

    大对象的文件上传在事务外完成；发布阶段只能把这里保存的内联数据或对象引用写入
    ``WorkflowArtifact``。对象键由运行、步骤、类型和内容哈希确定，Step 重试会复用
    同一对象，而不会产生多份临时版本。
    """

    artifact_type: str
    payload_sha256: str
    inline_json: dict[str, Any] | None = None
    object_ref: str | None = None
    object_sha256: str | None = None


class WorkflowArtifactStore:
    """以 WorkflowRun 为根保存可恢复的阶段产物。

    调用顺序必须是：handler 调用 :meth:`stage_json`，随后 ``persist_success`` 调用
    :meth:`persist_staged_json`。后者不执行任何网络或对象存储 I/O，因此可以与领域
    正式产物及 Step 成功检查点安全地处于同一个短 SQL 事务。
    """

    _INLINE_JSON_LIMIT = 64 * 1024
    _STAGED_OUTPUT_KEY = "__workflow_staged_artifact__"

    def stage_json(
        self,
        *,
        workflow_run_id: str,
        step_name: str,
        artifact_type: str,
        payload: dict[str, Any],
    ) -> StagedJsonArtifact:
        """在事务外暂存 JSON；小对象内联，大对象先写稳定对象键。"""
        if not artifact_type.strip() or not isinstance(payload, dict):
            raise ValueError("workflow_artifact_stage_invalid")
        serialized = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, default=str
        ).encode("utf-8")
        payload_sha256 = sha256(serialized).hexdigest()
        if len(serialized) <= self._INLINE_JSON_LIMIT:
            return StagedJsonArtifact(
                artifact_type=artifact_type,
                payload_sha256=payload_sha256,
                inline_json=dict(payload),
            )
        object_key = (
            f"workflow-artifacts/{workflow_run_id}/{step_name}/"
            f"{artifact_type}/{payload_sha256}.json"
        )
        from backend.app.storage.object_store import ObjectStore

        object_ref, object_sha256 = ObjectStore().put_bytes(
            object_key, serialized, "application/json"
        )
        return StagedJsonArtifact(
            artifact_type=artifact_type,
            payload_sha256=payload_sha256,
            object_ref=object_ref,
            object_sha256=object_sha256,
        )

    def stage_outcome(
        self,
        *,
        workflow_run_id: str,
        step_name: str,
        artifact_type: str,
        payload: Any,
    ) -> StagedJsonArtifact:
        """供 StepRunner 调用；确保步骤产物只允许 JSON 对象。"""
        if not isinstance(payload, dict):
            raise RuntimeError(f"workflow_artifact_payload_invalid:{artifact_type}")
        return self.stage_json(
            workflow_run_id=workflow_run_id,
            step_name=step_name,
            artifact_type=artifact_type,
            payload=payload,
        )

    @classmethod
    def attach_staged_output(
        cls, output_refs: dict[str, Any], staged: StagedJsonArtifact
    ) -> dict[str, Any]:
        """把仅供当前进程发布使用的暂存对象附到 StepOutcome，不写入检查点。"""
        return {**output_refs, cls._STAGED_OUTPUT_KEY: staged}

    @classmethod
    def staged_from_outcome(
        cls, outcome: Any, *, artifact_type: str
    ) -> StagedJsonArtifact:
        staged = (getattr(outcome, "output_refs", {}) or {}).get(
            cls._STAGED_OUTPUT_KEY
        )
        if not isinstance(staged, StagedJsonArtifact):
            raise RuntimeError(f"workflow_staged_artifact_missing:{artifact_type}")
        if staged.artifact_type != artifact_type:
            raise RuntimeError(f"workflow_staged_artifact_type_mismatch:{artifact_type}")
        return staged

    def persist_outcome_json(
        self,
        db: Session,
        *,
        workflow_run_id: str,
        artifact_type: str,
        outcome: Any,
    ) -> dict[str, str]:
        """供 Workflow 的 ``persist_success`` 使用的简写；不执行外部 I/O。"""
        return self.persist_staged_json(
            db,
            workflow_run_id=workflow_run_id,
            artifact_type=artifact_type,
            staged=self.staged_from_outcome(outcome, artifact_type=artifact_type),
        )
    def persist_staged_json(
        self,
        db: Session,
        *,
        workflow_run_id: str,
        artifact_type: str,
        staged: StagedJsonArtifact,
    ) -> dict[str, str]:
        """在 SQL 事务内登记已经暂存好的 Artifact，绝不访问对象存储。"""
        if staged.artifact_type != artifact_type:
            raise RuntimeError("workflow_artifact_type_mismatch")
        artifact = self._get_or_create(db, workflow_run_id, artifact_type)
        if staged.inline_json is not None:
            artifact.artifact_json = dict(staged.inline_json)
            artifact.object_ref = None
            artifact.object_sha256 = None
        else:
            if not staged.object_ref or not staged.object_sha256:
                raise RuntimeError("workflow_staged_artifact_object_missing")
            artifact.artifact_json = {
                "_storage": "object_store_v1",
                "object_ref": staged.object_ref,
                "object_sha256": staged.object_sha256,
                "payload_sha256": staged.payload_sha256,
            }
            artifact.object_ref = staged.object_ref
            artifact.object_sha256 = staged.object_sha256
        db.flush()
        return {"artifactId": artifact.artifact_id, "artifactType": artifact.artifact_type}

    def get_json(self, db: Session, artifact_id: str) -> dict[str, Any]:
        """读取阶段草稿；读取操作只能发生在 handler，不得位于发布事务。"""
        if _publishing_artifact.get():
            raise RuntimeError("workflow_artifact_read_in_publish_transaction")
        artifact = db.get(WorkflowArtifact, artifact_id)
        if artifact is None or artifact.artifact_json is None:
            raise RuntimeError(f"workflow_artifact_not_found:{artifact_id}")
        payload = dict(artifact.artifact_json)
        if payload.get("_storage") == "object_store_v1":
            object_ref = str(payload.get("object_ref") or "")
            if not object_ref:
                raise RuntimeError(f"workflow_artifact_object_ref_missing:{artifact_id}")
            from backend.app.storage.object_store import ObjectStore

            return ObjectStore().read_json(
                object_ref, f"workflow-artifacts/{artifact_id}/v1.json"
            )
        return payload

    def _get_or_create(
        self, db: Session, workflow_run_id: str, artifact_type: str
    ) -> WorkflowArtifact:
        run = db.scalar(
            select(WorkflowRun)
            .where(WorkflowRun.workflow_run_id == workflow_run_id)
            .with_for_update()
        )
        if run is None:
            raise RuntimeError("workflow_run_not_found")
        artifact = db.scalar(
            select(WorkflowArtifact)
            .where(
                WorkflowArtifact.workflow_run_id == workflow_run_id,
                WorkflowArtifact.artifact_type == artifact_type,
                WorkflowArtifact.version == 1,
            )
            .with_for_update()
        )
        if artifact is None:
            artifact = WorkflowArtifact(
                artifact_id=f"WFA_{uuid4().hex}",
                workflow_run_id=workflow_run_id,
                subject_type=run.subject_type,
                subject_id=run.subject_id,
                application_id=run.application_id,
                artifact_type=artifact_type,
                artifact_json={},
                version=1,
            )
            db.add(artifact)
            db.flush()
        return artifact