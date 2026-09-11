"""简历导入工作流：冻结 → PDF 解析 → 简历结构化 → 业务发布与岗位分发。"""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from backend.app.db.session import SessionLocal
from backend.app.infrastructure.workflow_runtime.artifact_store import WorkflowArtifactStore
from backend.app.models.entities import Application, Candidate, ResumeSubmission, SourceDocument, User, WorkflowRun
from backend.app.modules.applications.public import is_recruitment_in_progress
from backend.app.modules.candidates.public import CandidateRoutingService
from backend.app.modules.candidates.public import CandidateIntakeProcessService
from backend.app.modules.candidates.public import (
    CandidateIdentity,
    CandidateIdentityService,
    CandidateIntakeService,
    CandidateResumeRebuildService,
    ResumeSubmissionStatus,
    normalize_email,
    normalize_phone,

)
from backend.app.modules.candidates.domain.resume_submission_state_machine import ResumeRecoveryCode
from backend.app.modules.document_ingestion.contracts import (
    DocumentParseResult,
    ResumeMetadata,
    ResumePublishPrepared,
    ResumeSourceSnapshot,
    ResumeStructureResult,
)
from backend.app.modules.document_ingestion.parsing import PARSER_VERSION, DocumentParsingPipeline
from backend.app.modules.document_ingestion.parsing.document_blocks import blocks_from_markdown, normalized_block_artifact
from backend.app.modules.document_ingestion.processors import ExtractedResumeMetadata, ResumeDocumentProcessor
from backend.app.modules.document_ingestion.services.resume_structure_activity_service import (
    ResumeStructureActivityService,
)
from backend.app.shared.workflows import RecoveryAction, StepDefinition, StepErrorCategory, StepOutcome, StepPolicy, WorkflowSpec
from backend.app.storage.object_store import ObjectStore
from recruitment_ai_core.execution import model_call_scope

WORKFLOW_TYPE = "resume_document_import_workflow"


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _stable_id(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12].upper()
    return f"{prefix}_{digest}"


def _run_and_submission(db, run_id: str) -> tuple[WorkflowRun, ResumeSubmission, SourceDocument]:
    run = db.get(WorkflowRun, run_id)
    if run is None or run.subject_type != "resume_submission" or not run.subject_id:
        raise RuntimeError("workflow_subject_is_not_resume_submission")
    submission = db.get(ResumeSubmission, run.subject_id)
    if submission is None:
        raise RuntimeError("resume_submission_not_found")
    document = db.get(SourceDocument, submission.source_document_id)
    if document is None or document.document_type != "resume":
        raise RuntimeError("resume_source_document_not_found")
    return run, submission, document


def _input(run_id: str) -> dict[str, Any]:
    """输入哈希只使用提交、源文件哈希和模式，保证重试不会误复用其他版本。"""
    with SessionLocal() as db:
        run, submission, document = _run_and_submission(db, run_id)
        return {
            "submissionId": submission.resume_submission_id,
            "sourceDocumentId": document.source_document_id,
            "sourceSha256": document.source_sha256,
            "intakeMode": submission.intake_mode,
            "parseStrategy": submission.parse_strategy,
            "parserVersion": PARSER_VERSION,
        }


def _freeze_handler(context) -> StepOutcome:
    """步骤 1：读取并校验稳定来源，不做任何业务状态写入。"""
    with SessionLocal() as db:
        run, submission, document = _run_and_submission(db, context.workflow_run_id)
        source = ResumeSourceSnapshot(
            submissionId=submission.resume_submission_id,
            sourceDocumentId=document.source_document_id,
            objectRef=document.object_ref,
            filename=document.original_filename,
            sourceSha256=document.source_sha256,
            nameOverride=submission.candidate_name_override,
            intakeMode=submission.intake_mode,
            parseStrategy=submission.parse_strategy,
            manualCorrectionRef=submission.manual_correction_ref,
            candidateId=submission.candidate_id,
            existingText=submission.parsed_text or "",
            # 迁移期只有历史任务仍在旧列保存解析元数据；新任务使用 parse_result_json。
            existingMetadata=dict(submission.parse_result_json or {}),
            existingStructureRef=submission.structure_result_ref,
            existingStructureSha256=submission.structure_result_sha256,
            existingStructureSchemaVersion=submission.structure_schema_version,
            existingStructureStatus=str((submission.structure_metadata_json or {}).get("structure_status") or "") or None,
            existingStructureMetadata=dict(submission.structure_metadata_json or {}),
            parserVersion=PARSER_VERSION,
        )
        return StepOutcome.succeeded(data=source.artifact_json())


def _freeze_persist(db, context, outcome) -> dict[str, Any]:
    return WorkflowArtifactStore().persist_outcome_json(
        db, workflow_run_id=context.workflow_run_id,
        artifact_type="resume_source_manifest", outcome=outcome,
    )


def _read_artifact(context, step_name: str) -> dict[str, Any]:
    refs = context.previous_output_refs.get(step_name) or {}
    artifact_id = str(refs.get("artifactId") or "")
    if not artifact_id:
        raise RuntimeError(f"resume_previous_artifact_missing:{step_name}")
    with SessionLocal() as db:
        return WorkflowArtifactStore().get_json(db, artifact_id)


def _parse_handler(context) -> StepOutcome:
    """步骤 2：提交或恢复 PDF 解析，仅在 MinerU 已完成后产出文本草稿。

    第一次执行只提交 MinerU 并把 batchId 写入当前检查点；后续 Worker 每次只轮询
    一次。这样服务重启、租约切换或队列重试都不会重复上传同一份 PDF。
    """
    source_contract = ResumeSourceSnapshot.model_validate(
        _read_artifact(context, "freeze_resume_sources")
    )
    source = source_contract.artifact_json()
    metadata = dict(source_contract.existing_metadata or {})
    resume_text = str(source.get("existingText") or "")
    document_id = str(source["sourceDocumentId"])
    submission_id = str(source["submissionId"])
    blocks_ref = str(
        metadata.get("documentBlocksRef")
        or metadata.get("document_blocks_ref")
        or ""
    )
    # 人工校正只消费用户已选择的不可变原文块，不会依赖新的 PDF 解析规则。
    # 即使历史解析记录缺少当前 ``PARSER_VERSION``，只要块索引和原文都在，
    # 也必须直接继续；否则用户选择“校正”仍会被不必要地送回 MinerU。
    manual_correction_reuse = source_contract.intake_mode == "manual_correction"
    if (
        source_contract.parse_strategy == "reuse_verified_parse"
        and resume_text
        and blocks_ref
        and (
            manual_correction_reuse
            or (metadata.get("parserVersion") or metadata.get("parser_version"))
            == PARSER_VERSION
        )
    ):
        parser_metadata = {**metadata, "reused_parsed_text": True}
    else:
        path = ObjectStore().materialize(
            str(source["objectRef"]),
            f"documents/{document_id}/{source['filename']}",
        )
        data = path.read_bytes()
        pipeline = DocumentParsingPipeline()
        if pipeline.uses_async_mineru():
            if not context.external_job_id:
                submitted = pipeline.submit_mineru(
                    data,
                    str(source["filename"]),
                    data_id=document_id,
                    timeout_seconds=context.remaining_timeout_seconds(),
                    idempotency_key=context.external_request_id,
                )
                return StepOutcome.waiting_external(
                    external_job_id=submitted.batch_id,
                    retry_after_seconds=5,
                )
            poll = pipeline.poll_mineru(
                batch_id=context.external_job_id,
                filename=str(source["filename"]),
                data_id=document_id,
                timeout_seconds=context.remaining_timeout_seconds(),
                idempotency_key=context.external_request_id,
            )
            if poll.state != "done":
                return StepOutcome.waiting_external(
                    external_job_id=context.external_job_id,
                    retry_after_seconds=5,
                )
            parsed = pipeline.complete_mineru(
                data,
                str(source["filename"]),
                poll,
                timeout_seconds=context.remaining_timeout_seconds(),
                idempotency_key=context.external_request_id,
            )
        else:
            parsed = pipeline.parse_without_mineru(
                data,
                str(source["filename"]),
                timeout_seconds=context.remaining_timeout_seconds(),
                idempotency_key=context.external_request_id,
            )
        resume_text = parsed.text
        parser_metadata = dict(parsed.metadata)
        blocks = normalized_block_artifact(
            parsed.blocks
            or blocks_from_markdown(
                resume_text,
                source_parser=str(parser_metadata.get("provider") or "unknown"),
            ),
            parser=str(parser_metadata.get("provider") or "unknown"),
            source_sha256=str(source.get("sourceSha256") or ""),
        )
        blocks_ref, blocks_sha = ObjectStore().put_json(
            f"documents/{document_id}/submissions/{submission_id}/document_blocks_v1.json",
            blocks,
        )
        parser_metadata.update({
            "document_blocks_ref": blocks_ref,
            "document_blocks_sha256": blocks_sha,
            "document_blocks_schema_version": blocks["schema_version"],
        })
    if not blocks_ref:
        blocks = normalized_block_artifact(
            blocks_from_markdown(resume_text, source_parser="legacy_text"),
            parser="legacy_text",
        )
        blocks_ref, blocks_sha = ObjectStore().put_json(
            f"documents/{document_id}/submissions/{submission_id}/document_blocks_v1.json",
            blocks,
        )
        parser_metadata.update({
            "document_blocks_ref": blocks_ref,
            "document_blocks_sha256": blocks_sha,
            "document_blocks_schema_version": blocks["schema_version"],
        })
    parse_result = DocumentParseResult(
        source=source_contract,
        resumeText=resume_text,
        parserMetadata=parser_metadata,
        documentBlocksRef=blocks_ref,
        documentBlocksSha256=str(parser_metadata.get("document_blocks_sha256") or "") or None,
    )
    return StepOutcome.succeeded(data=parse_result.artifact_json())

def _parse_persist(db, context, outcome) -> dict[str, Any]:
    """短事务：保存解析产物、推进 Submission/Document 到 extracting。"""
    parse_result = DocumentParseResult.model_validate(dict(outcome.data or {}))
    _, submission, document = _run_and_submission(db, context.workflow_run_id)
    candidate = db.get(Candidate, submission.candidate_id) if submission.candidate_id else None
    now = _now()
    if submission.status == ResumeSubmissionStatus.QUEUED.value:
        CandidateIntakeProcessService.begin_parsing(
            submission, document=document, candidate=candidate, now=now
        )
    if submission.status != ResumeSubmissionStatus.EXTRACTING.value:
        CandidateIntakeProcessService.begin_extracting(
            submission, document=document, candidate=candidate, now=now
        )
    submission.parsed_text = parse_result.resume_text
    # 解析摘要有独立合同；解析结果已使用具名合同保存。
    submission.parse_result_json = parse_result.persistence_json()
    submission.updated_at = now
    return WorkflowArtifactStore().persist_outcome_json(
        db, workflow_run_id=context.workflow_run_id,
        artifact_type="resume_parse_result", outcome=outcome,
    )


def _structure_activity_outcome(batch) -> StepOutcome:
    """活动失败只延迟当前结构化 Step；已完成的项目 WorkUnit 会自动复用。"""
    if batch.status == "blocked":
        return StepOutcome.blocked(
            error_code=batch.error_code or "resume_structure_review_required",
            error_message=batch.error_message or "简历结构化缺少可用来源，需要人工确认",
            recovery_action=RecoveryAction.REVIEW_REQUIRED,
        )
    if batch.status == "retry_wait":
        return StepOutcome.activity_retry_wait(
            error_code=batch.error_code or "resume_structure_activity_retry_wait",
            error_message=batch.error_message or "简历结构化活动等待重试",
            retry_after_seconds=max(1, int(batch.retry_after_seconds or 15)),
        )
    return StepOutcome.failed(
        # 对 Candidate Intake 状态机而言，所有不可恢复结构化子活动统一是结构化失败；
        # 底层活动错误码仍保存在检查点中供技术排障。
        error_code="resume_structure_unusable",
        error_message=f"resume_structure_failed:{batch.error_message or 'activity_failed'}",
        error_category=batch.error_category or StepErrorCategory.EXTERNAL_PERMANENT,
    )


def _structure_handler(context) -> StepOutcome:
    """步骤 3：按外部调用拆分结构化活动，并将最终结构结果写入稳定对象键。

    MinerU 解析仍属于上一个 Step 的异步外部任务；这里的恢复边界是基础信息、轮廓
    修复、每段经历 WorkUnit 与技能声明。任一活动失败不会重跑已成功的同级活动。
    """
    parse_result = DocumentParseResult.model_validate(
        _read_artifact(context, "parse_resume_document")
    )
    source = parse_result.source
    document_id = source.source_document_id
    submission_id = source.submission_id
    if source.existing_structure_ref:
        structure = ObjectStore().read_json(
            source.existing_structure_ref,
            # object_ref 指向旧 Submission 的原始对象；cache_key 仅用于 MinIO
            # 本地缓存，使用标准工件文件名，不能伪造一个新 Submission 的来源路径。
            f"documents/{document_id}/submissions/{submission_id}/resume_structure_v2.json",
        )
        status = str(structure.get("status") or "")
        if status not in {"passed", "repaired"}:
            raise RuntimeError("resume_reusable_structure_not_publishable")
        stored_metadata = dict(source.existing_structure_metadata or {})
        # 当前 Submission 直接保存 ResumeMetadata，较早工件才包在 metadata 下；
        # 质量摘要是页面投影字段，不能传入严格的 ResumeMetadata 合同。
        metadata_payload = dict(stored_metadata)
        metadata_payload.pop("processing_quality", None)
        metadata = ResumeMetadata.model_validate(metadata_payload)
        structure_result = ResumeStructureResult(
            parse=parse_result,
            structureRef=source.existing_structure_ref,
            structureSha256=str(source.existing_structure_sha256 or ""),
            structureSchemaVersion=str(source.existing_structure_schema_version or structure.get("schema_version") or ""),
            structureStatus=status,
            structureMethod=str(structure.get("method") or "reused_structure"),
            structureWarnings=list((structure.get("validation") or {}).get("warnings") or []),
            metadata=metadata,
            extractionTrace={
                "mode": "publish_retry_reused_structure",
                "processing_quality": dict(stored_metadata.get("processing_quality") or {}),
            },
        )
        return StepOutcome.succeeded(data=structure_result.artifact_json())
    resume_text = parse_result.resume_text
    blocks_ref = parse_result.document_blocks_ref
    blocks = ObjectStore().read_json(
        blocks_ref, f"documents/{document_id}/submissions/{submission_id}/document_blocks_v1.json"
    )
    manual_correction: dict[str, Any] | None = None
    if source.manual_correction_ref:
        manual_correction = ObjectStore().read_json(
            source.manual_correction_ref,
            f"documents/{document_id}/submissions/{submission_id}/manual_correction_v1.json",
        )
    with model_call_scope(context.external_request_id, "resume_structure"):
        batch = ResumeStructureActivityService().structure(
            context,
            candidate_id=_stable_id("STRUCT", context.workflow_run_id),
            resume_text=resume_text,
            document_blocks=list(blocks.get("blocks") or []),
            name_override=source.name_override,
            manual_correction=manual_correction,
        )
    # degraded 仍使用同一份 ResumeProfile 合同继续发布；质量摘要已随结构结果保存。
    if not batch.is_usable:
        return _structure_activity_outcome(batch)

    structure = dict(batch.results["structure"])
    metadata = ResumeMetadata.model_validate(dict(batch.results["metadata"]))
    trace = dict(batch.results["trace"])
    structure_ref, structure_sha = ObjectStore().put_json(
        f"documents/{document_id}/submissions/{submission_id}/resume_structure_v2.json", structure
    )
    structure_result = ResumeStructureResult(
        parse=parse_result,
        structureRef=structure_ref,
        structureSha256=structure_sha,
        structureSchemaVersion=str(structure["schema_version"]),
        structureStatus=str(structure["status"]),
        structureMethod=structure.get("method"),
        structureWarnings=list((structure.get("validation") or {}).get("warnings") or []),
        metadata=metadata,
        extractionTrace=trace,
    )
    return StepOutcome.succeeded(data=structure_result.artifact_json())


def _processing_quality(extraction_trace: dict[str, Any]) -> dict[str, Any]:
    """将活动降级结论压缩为可公开展示的业务摘要。"""
    persisted = extraction_trace.get("processing_quality")
    if isinstance(persisted, dict):
        return dict(persisted)
    activity_quality = dict(
        extraction_trace.get("activity_quality")
        or extraction_trace.get("activityQuality")
        or {}
    )
    degraded_parts: list[str] = []
    labels = {
        "metadata": "基础信息",
        "outline": "经历轮廓",
        "workUnits": "项目工作事实",
        "skillClaims": "技能声明",
    }
    for name, value in activity_quality.items():
        outcomes = dict(value.get("outcomeKinds") or {}) if isinstance(value, dict) else {}
        if any(str(outcome) == "degraded" for outcome in outcomes.values()):
            degraded_parts.append(labels.get(str(name), str(name)))
    if not degraded_parts:
        return {}
    return {
        "status": "degraded",
        "message": "部分信息已按保守规则处理，缺失字段会保留为空，不会被推断为负面事实。",
        "parts": degraded_parts,
    }


def _structure_persist(db, context, outcome) -> dict[str, Any]:
    """短事务：保存结构化结果引用；正式 ResumeProfile 仍只能在发布 Step 创建。"""
    structure_result = ResumeStructureResult.model_validate(dict(outcome.data or {}))
    _, submission, _ = _run_and_submission(db, context.workflow_run_id)
    submission.parsed_text = structure_result.parse.resume_text
    submission.structure_result_ref = structure_result.structure_ref
    submission.structure_result_sha256 = structure_result.structure_sha256
    submission.structure_schema_version = structure_result.structure_schema_version
    metadata = structure_result.metadata.model_dump(mode="json")
    quality = _processing_quality(structure_result.extraction_trace)
    if quality:
        metadata["processing_quality"] = quality
    submission.structure_metadata_json = metadata
    submission.parse_result_json = structure_result.parse.persistence_json()
    submission.updated_at = _now()
    return WorkflowArtifactStore().persist_outcome_json(
        db, workflow_run_id=context.workflow_run_id,
        artifact_type="resume_structure_result", outcome=outcome,
    )

def _metadata_from(value: dict[str, Any]) -> ExtractedResumeMetadata:
    """将已通过 ResumeMetadata 合同的数据转为现有发布服务使用的值对象。"""
    raw = dict(value or {})
    # Step Artifact 只接受当前版本的扁平 ResumeMetadata 合同；旧的包装形状
    # 不再兼容，避免发布阶段悄悄绕过合同升级。
    data = raw
    return ExtractedResumeMetadata(
        candidate_name=str(data.get("candidate_name") or ""),
        phone=data.get("phone"), email=data.get("email"),
        current_title=data.get("current_title"),
        relevant_experience_years=data.get("relevant_experience_years"),
        education_records=[
            dict(item)
            for item in list(data.get("education_records") or [])
            if isinstance(item, dict)
        ],
        qualification_records=[
            dict(item)
            for item in list(data.get("qualification_records") or [])
            if isinstance(item, dict)
        ],
        age=data.get("age"), metadata=dict(data.get("metadata") or {}),
    )


def _resume_metadata_view(metadata: ExtractedResumeMetadata) -> dict[str, Any]:
    return {**asdict(metadata), "metadata": dict(metadata.metadata or {})}


def _publish_handler(context) -> StepOutcome:
    """步骤 4：读取已验证结构化结果，为同一短事务的正式发布准备合同输入。"""
    structure_result = ResumeStructureResult.model_validate(
        _read_artifact(context, "structure_resume")
    )
    document_id = structure_result.parse.source.source_document_id
    submission_id = structure_result.parse.source.submission_id
    structure = ObjectStore().read_json(
        structure_result.structure_ref,
        f"documents/{document_id}/submissions/{submission_id}/resume_structure_v2.json",
    )
    if str(structure.get("status") or "") not in {"passed", "repaired"}:
        reason = str(structure.get("failure_reason") or "resume_structure_failed")
        return StepOutcome.failed(
            error_code="resume_structure_unusable",
            error_message=f"resume_structure_failed:{reason}",
            error_category=StepErrorCategory.VALIDATION,
        )
    prepared = ResumePublishPrepared(structureResult=structure_result, structure=structure)
    return StepOutcome.succeeded(data=prepared.artifact_json())

def _publish_persist(db, context, outcome) -> dict[str, Any]:
    """短事务：仅发布已通过校验的 ResumeProfile，并触发后续岗位分发/重评分。"""
    prepared = ResumePublishPrepared.model_validate(dict(outcome.data or {}))
    run, submission, document = _run_and_submission(db, context.workflow_run_id)
    if run.status != "running" or run.lease_owner != context.worker_id:
        raise RuntimeError("workflow_lease_lost")
    structure = dict(prepared.structure or {})
    metadata = _metadata_from(prepared.structure_result.metadata.model_dump(mode="json"))
    candidate = db.get(Candidate, submission.candidate_id) if submission.candidate_id else None
    if str(structure.get("status") or "") not in {"passed", "repaired"}:
        # 理论上 failed 已由 handler 截获；这里保留防御性校验，避免未知结构化状态
        # 被错误发布为可供初筛的 ResumeProfile。
        raise RuntimeError("resume_structure_not_publishable")

    user = db.get(User, submission.uploaded_by)
    if user is None:
        raise RuntimeError("resume_submission_context_not_found")
    intake = CandidateIntakeService(db)
    if submission.intake_mode in {"reparse", "replacement", "manual_correction"}:
        if candidate is None:
            raise RuntimeError("candidate_resume_rebuild_candidate_not_found")
        profile = intake.persist_profile(
            candidate=candidate, submission=submission, document=document,
            structure_result=structure, metadata=metadata, force_new_version=True,
        )
        CandidateIntakeProcessService.complete_submission(
            submission, document=document, candidate=candidate, now=_now()
        )
        ids = CandidateResumeRebuildService(db).rebuild_applications(
            candidate=candidate, submission=submission, profile=profile,
            run_id=run.workflow_run_id, user=user,
        )
        next_workflow = ""
        if ids:
            # 重建服务已通过 Application 公开契约完成版本切换和重评分入队，保留原有
            # 招聘主状态；本 workflow 仅推进 Candidate 自身生命周期。
            CandidateIntakeProcessService.activate_candidate(candidate, now=_now())
        else:
            # 历史首次导入可能在创建 Application 前失败。重新解析成功后若仍没有
            # 任何有效申请，必须补走岗位分发，而不能将“重建 0 条”视为最终完成。
            routing_run = CandidateRoutingService(db).enqueue_submission(
                submission, triggered_by=user.user_id
            )
            next_workflow = routing_run.workflow_run_id
            CandidateIntakeProcessService.activate_candidate(candidate, now=_now())
        # 重建完成状态只由当前 ResumeSubmission 表达；Candidate 不再保存过程 payload。
        candidate.updated_at = submission.updated_at = document.updated_at = _now()
        return {
            "submissionId": submission.resume_submission_id,
            "resumeProfileId": profile.resume_profile_id,
            "applicationCount": str(len(ids)),
            "nextWorkflow": "candidate_routing_workflow" if next_workflow else "",
            "nextWorkflowRunId": next_workflow,
        }

    identity = CandidateIdentity(
        name=re.sub(r"\s+", "", str(metadata.candidate_name or "")).casefold(),
        school=re.sub(r"\s+", "", str(metadata.school or "")).casefold(),
        major=re.sub(r"\s+", "", str(metadata.major or "")).casefold(),
        phone=normalize_phone(metadata.phone), email=normalize_email(metadata.email),
    )
    matches, conflict = CandidateIdentityService(db).find_matches(identity)
    if len(matches) > 1:
        now = _now()
        CandidateIntakeProcessService.require_review(
            submission,
            review_kind="duplicate_ambiguous",
            reason="存在多个可能的同一候选人，需人工确认",
            document=document,
            candidate=candidate,
            now=now,
        )
        submission.review_context_json = {
            **dict(submission.review_context_json or {}),
            "identityMatch": {"status": "ambiguous", "candidateIds": [item.candidate_id for item in matches]},
        }
        return {"submissionId": submission.resume_submission_id, "result": "duplicate_review_required"}
    if len(matches) == 1:
        existing = matches[0]
        applications = list(db.scalars(select(Application).where(Application.candidate_id == existing.candidate_id, Application.deleted_at.is_(None))))
        is_blocked = any(is_recruitment_in_progress(app.status) for app in applications)
        now = _now()
        # 身份确认前必须保留首次上传创建的占位 Candidate。若在这里提前改绑到已有
        # Candidate，该 Submission 就不是对方的 current version，读模型会正确地
        # 隐藏所有动作，最终形成用户无法处理的待确认任务。只有用户选择“采用本次
        # 简历”后，命令事务才切换 candidate_id 和当前简历指针。
        CandidateIntakeProcessService.require_review(
            submission,
            review_kind="duplicate_blocked" if is_blocked else "duplicate_match",
            reason="该候选人正在招聘流程中，不能上传新版简历" if is_blocked else "检测到已有候选人，请选择覆盖旧简历或放弃本次导入",
            document=document,
            candidate=candidate,
            now=now,
            recovery_code=(ResumeRecoveryCode.DUPLICATE_BLOCKED.value if is_blocked else None),
            recovery_context=(
                {"candidateId": existing.candidate_id, "activeApplicationCount": sum(is_recruitment_in_progress(app.status) for app in applications)}
                if is_blocked else None
            ),
        )
        submission.review_context_json = {
            **dict(submission.review_context_json or {}),
            "identityMatch": {"status": "matched", "candidateId": existing.candidate_id, "identityConflict": conflict},
        }
        return {"submissionId": submission.resume_submission_id, "result": "duplicate_review_required"}

    candidate = intake.ensure_placeholder(submission, document, candidate_id=_stable_id("CAND", submission.resume_submission_id))
    profile = intake.persist_profile(candidate=candidate, submission=submission, document=document, structure_result=structure, metadata=metadata)
    CandidateIntakeProcessService.complete_submission(
        submission, document=document, candidate=candidate, now=_now()
    )
    CandidateRoutingService(db).enqueue_submission(submission, triggered_by=user.user_id)
    return {"submissionId": submission.resume_submission_id, "candidateId": candidate.candidate_id, "resumeProfileId": profile.resume_profile_id, "nextWorkflow": "candidate_routing_workflow"}


def build_resume_document_import_spec(transition_handler, blocked_handler=None) -> WorkflowSpec:
    """声明简历导入的恢复步骤。

    1. 冻结 ResumeSubmission 与上传文件；
    2. 解析 PDF 为文本和文档块；
    3. 结构化出候选人简历事实；
    4. 发布 ResumeProfile，或进入人工确认/岗位分发分支。
    """
    return WorkflowSpec(
        workflow_type=WORKFLOW_TYPE,
        handler=None,
        transition_handler=transition_handler,
        blocked_handler=blocked_handler,
        definition_version=1,
        steps=(
            # 步骤 1：冻结本次提交、源文件哈希及已有解析结果，防止串用其他版本。
            StepDefinition("freeze_resume_sources", 1, _freeze_handler, _input, StepPolicy(timeout_seconds=30, max_attempts=2, total_deadline_seconds=180), _freeze_persist, artifact_type="resume_source_manifest"),
            # 步骤 2：解析 PDF 文本和文档块；解析结果先写入可恢复工件。
            StepDefinition(
                "parse_resume_document", 2, _parse_handler, _input,
                # MinerU 已生成结果后下载对象仍可能短暂不可用。恢复时会复用
                # batchId，只轮询和下载，绝不重新上传 PDF；因此给该外部 Step
                # 更合理的 5 次指数退避窗口，而不是十余秒后直接判定失败。
                StepPolicy(
                    timeout_seconds=180, max_attempts=5, total_deadline_seconds=900,
                    initial_backoff_seconds=10, max_backoff_seconds=60, external=True,
                ),
                _parse_persist, artifact_type="resume_parse_result",
            ),
            # 步骤 3：调用结构化能力，必要时用视觉解析兜底；仍只保存阶段产物。
            StepDefinition("structure_resume", 3, _structure_handler, _input, StepPolicy(timeout_seconds=240, max_attempts=3, total_deadline_seconds=1200, external=True), _structure_persist, artifact_type="resume_structure_result"),
            # 步骤 4：发布待确认结果或 ResumeProfile，并触发岗位分发/重评分。
            StepDefinition("publish_resume_result", 4, _publish_handler, _input, StepPolicy(timeout_seconds=90, max_attempts=2, total_deadline_seconds=300), _publish_persist),
        ),
    )
