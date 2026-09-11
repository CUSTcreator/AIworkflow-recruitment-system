"""岗位文档导入：提取表格 → 保存提取草稿 → 发布待确认 JobDraft。"""
from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
import re
from uuid import uuid4

from sqlalchemy import delete, select

from backend.app.db.session import SessionLocal
from backend.app.infrastructure.workflow_runtime.artifact_store import WorkflowArtifactStore
from backend.app.models.entities import Department, JobDocumentImport, JobDraft, SourceDocument, WorkflowArtifact, WorkflowRun
from backend.app.modules.document_ingestion.processors import JobDocumentProcessor
from backend.app.modules.document_ingestion.processors.job_document_processor import HEADER_ALIASES
from backend.app.modules.document_ingestion.processors import JobExtractionRepairService
from backend.app.modules.document_ingestion.processors import JobHeaderRepairService
from backend.app.modules.document_ingestion.services.job_document_activity_service import (
    JobDocumentActivityService,
)
from backend.app.modules.document_ingestion.services.job_requirement_classification_activity_service import (
    JobRequirementClassificationActivityService,
)
from backend.app.shared.workflows import (
    RecoveryAction, StepDefinition, StepErrorCategory, StepOutcome, StepPolicy, WorkflowSpec,
)
from backend.app.storage.object_store import ObjectStore
from backend.app.modules.document_ingestion.parsing import ExcelDocumentParser
from backend.app.modules.document_ingestion.parsing.document_blocks import blocks_from_markdown, normalized_block_artifact
from recruitment_ai_core.execution import model_call_scope
from recruitment_ai_core.screening_scoring.resume_experience.model_selector import recommend_preset_model
from backend.app.modules.jobs.job_recovery import JobRecoveryCode
from backend.app.modules.document_ingestion.services.job_document_import_process_service import JobDocumentImportProcessService

WORKFLOW_TYPE = "job_document_import_workflow"


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _normalize_department_name(value: str) -> str:
    """Use the same exact-name normalization during import and confirmation."""
    return re.sub(r"[\s:：()（）/／_-]+", "", str(value or "")).casefold()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16].upper()}"


def _required_text_list(value: object, field_name: str) -> list[str]:
    """发布前收紧工件契约：职责和资格必须是文本数组，不能退化为字符串。"""
    if not isinstance(value, list):
        raise RuntimeError(f"job_document_{field_name}_must_be_list")
    return [str(item).strip() for item in value if str(item).strip()]


def _input(run_id: str) -> dict:
    with SessionLocal() as db:
        run = db.get(WorkflowRun, run_id)
        import_task = db.get(JobDocumentImport, run.subject_id) if run and run.subject_id else None
        document = db.get(SourceDocument, import_task.source_document_id) if import_task else None
        if run is None or import_task is None or document is None:
            raise RuntimeError("job_document_input_missing")
        return {"jobDocumentImportId": import_task.job_document_import_id, "sourceDocumentId": document.source_document_id, "sourceSha256": document.source_sha256, "objectRef": document.object_ref}


def _classification_input(run_id: str) -> dict:
    """分类 Step 输入包含解析工件引用，防止复用另一轮解析结果。"""
    base = _input(run_id)
    with SessionLocal() as db:
        artifact = db.scalar(
            select(WorkflowArtifact)
            .where(
                WorkflowArtifact.workflow_run_id == run_id,
                WorkflowArtifact.artifact_type == "job_document_extraction",
            )
            .order_by(WorkflowArtifact.version.desc())
        )
    if artifact is None:
        raise RuntimeError("job_requirement_classification_extraction_missing")
    return {
        **base,
        "extractionArtifactId": artifact.artifact_id,
        "extractionArtifactSha256": artifact.object_sha256
        or str((artifact.artifact_json or {}).get("sourceSha256") or ""),
    }


def _activity_outcome(batch, result_key: str | None = None) -> StepOutcome:
    """将子活动结论映射为父 Step 合同；活动退避不消耗整个提取 Step 的次数。"""
    if batch.is_usable:
        return StepOutcome.succeeded(data=(batch.results.get(result_key) if result_key else batch.results))
    if batch.status == "blocked":
        return StepOutcome.blocked(
            error_code=batch.error_code or "job_document_review_required",
            error_message=batch.error_message or "岗位文档需要人工确认后继续",
            recovery_action=RecoveryAction.REVIEW_REQUIRED,
        )
    if batch.status == "retry_wait":
        return StepOutcome.activity_retry_wait(
            error_code=batch.error_code or "job_document_activity_retry_wait",
            error_message=batch.error_message or "岗位文档修复等待重试",
            retry_after_seconds=max(1, int(batch.retry_after_seconds or 15)),
        )
    return StepOutcome.failed(
        error_code=batch.error_code or "job_document_activity_failed",
        error_message=batch.error_message or "岗位文档修复失败",
        error_category=batch.error_category or StepErrorCategory.EXTERNAL_PERMANENT,
    )


def _extract_handler(context) -> StepOutcome:
    """事务外规则提取 Excel，并仅将不确定修复交给独立活动。

    同一 Worker 重试会重新读取原始文件，但已成功的表头/字段修复从 Activity Artifact
    复用；因此不会依赖内存 Workbook，也不会再次请求 LLM。
    """
    with SessionLocal() as db:
        run = db.get(WorkflowRun, context.workflow_run_id)
        import_task = db.get(JobDocumentImport, run.subject_id) if run and run.subject_id else None
        document = db.get(SourceDocument, import_task.source_document_id) if import_task else None
        if run is None or import_task is None or document is None:
            raise RuntimeError("job_document_input_missing")
        retry_mode = str((run.input_json or {}).get("retryMode") or "")
        repair_action = str((run.input_json or {}).get("repairAction") or "")
        repair_context = dict((run.input_json or {}).get("repairContext") or {})
        extraction_artifact_id = str((run.input_json or {}).get("extractionArtifactId") or "")
        object_ref, filename = document.object_ref, document.original_filename
        document_id, sha = document.source_document_id, document.source_sha256

    if retry_mode == "publish_drafts":
        if not extraction_artifact_id:
            raise RuntimeError("job_document_publish_retry_artifact_missing")
        with SessionLocal() as db:
            payload = WorkflowArtifactStore().get_json(db, extraction_artifact_id)
        if str(payload.get("sourceSha256") or "") != sha or not isinstance(payload.get("jobs"), list):
            raise RuntimeError("job_document_publish_retry_artifact_invalid")
        # 发布重试仍经过提取 Step 的标准输出合同，但不读取文件、不调用 LLM。
        # 新运行会登记自己的工件引用，使后续检查点保持自包含和可审计。
        return StepOutcome.succeeded(data=payload)

    path = ObjectStore().materialize(object_ref, f"documents/{document_id}/{filename}")
    workbook = ExcelDocumentParser().parse(path.read_bytes(), filename)
    processor = JobDocumentProcessor()
    activities = JobDocumentActivityService()
    header_repair: dict = {"mode": "not_required", "repaired_sheets": []}
    try:
        # 人工表头映射在本次读取的 Workbook 上应用，后续仍走同一套规则提取。
        if retry_mode == "manual_repair" and repair_action == "review_job_headers":
            _apply_manual_header_mapping(workbook, repair_context)
        jobs, extraction_metadata = processor.extract(workbook)
        # 缺少部门列或部门单元格为空都允许先生成岗位草稿；发布时标记为
        # unmatched，确认页会让用户选择已有部门或按原有规则创建新部门。
        # 只有连岗位表头都无法识别时才进入表头修复/人工映射。
        needs_header_repair = any(item.get("status") == "skipped" for item in extraction_metadata.get("sheets") or [])
    except RuntimeError as error:
        if str(error) != "job_spreadsheet_header_not_found":
            raise
        jobs, extraction_metadata, needs_header_repair = [], {"sheets": []}, True

    if needs_header_repair and not (retry_mode == "manual_repair" and repair_action == "review_job_headers"):
        with model_call_scope(context.external_request_id, "job_document_header_repair"):
            header_batch = activities.repair_headers(
                context, workbook=workbook, source_document_id=document_id, source_sha256=sha,
                preserve_existing=bool(jobs),
            )
        if not header_batch.is_usable:
            return _activity_outcome(header_batch)
        header_repair = dict(header_batch.results.get("header_repair") or {})
        JobHeaderRepairService.apply_repair_plans(
            workbook, list(header_repair.get("repair_plans") or [])
        )
        try:
            jobs, extraction_metadata = processor.extract(workbook)
        except RuntimeError:
            # 表头修复失败时保留规则已经识别的岗位；只有一条岗位都没有才阻塞。
            if not jobs:
                return StepOutcome.blocked(
                    error_code="job_document_header_review_required",
                    error_message="岗位文档没有可识别的岗位输入，请确认表头",
                    recovery_action=RecoveryAction.REVIEW_REQUIRED,
                )

    with model_call_scope(context.external_request_id, "job_document_field_repair"):
        if retry_mode == "manual_repair" and repair_action == "review_job_fields":
            _apply_manual_field_repairs(jobs, repair_context.get("fields") or {})
        field_batch = activities.repair_job_fields(
            context, jobs=jobs, source_document_id=document_id, source_sha256=sha
        )
    if not field_batch.is_usable:
        return _activity_outcome(field_batch)
    jobs = activities.apply_field_repairs(jobs, field_batch.results)
    repair_metadata = list(field_batch.results.values())

    serialized = [asdict(item) for item in jobs]
    parsed_text = "\n\n".join(str(item["source_text"]) for item in serialized)
    parser = str((workbook.metadata or {}).get("provider") or "unknown")
    block_artifact = normalized_block_artifact(
        blocks_from_markdown(parsed_text, source_parser=parser),
        parser=parser,
        source_sha256=sha,
    )
    blocks_ref, blocks_sha256 = ObjectStore().put_json(
        f"documents/{document_id}/document_blocks_v1.json", block_artifact
    )
    return StepOutcome.succeeded(data={
        "sourceDocumentId": document_id,
        "sourceSha256": sha,
        "parsedText": parsed_text,
        "parserProvider": str((workbook.metadata or {}).get("provider") or "unknown"),
        "documentBlocksRef": blocks_ref,
        "documentBlocksSha256": blocks_sha256,
        "documentBlocksSchemaVersion": block_artifact["schema_version"],
        "jobs": serialized,
        "extraction": extraction_metadata,
        "headerRepair": header_repair,
        "repair": repair_metadata,
        "repairQuality": {
            "outcomeKinds": dict(field_batch.outcome_kinds),
            "summaries": dict(field_batch.quality_summaries),
        },
    })


def _classify_job_requirements_handler(context) -> StepOutcome:
    """确认前分类：handler 只读取上一步工件并编排 Activity。"""
    refs = context.previous_output_refs.get("extract_job_document") or {}
    artifact_id = str(refs.get("artifactId") or "")
    if not artifact_id:
        raise RuntimeError("job_requirement_classification_artifact_missing")
    with SessionLocal() as db:
        extraction = WorkflowArtifactStore().get_json(db, artifact_id)
    jobs = list(extraction.get("jobs") or [])
    if not jobs:
        return StepOutcome.blocked(
            error_code="job_requirement_classification_jobs_missing",
            error_message="没有可用于生成硬筛条件的岗位内容",
            recovery_action=RecoveryAction.REVIEW_REQUIRED,
        )
    batch = JobRequirementClassificationActivityService().classify(
        context,
        jobs,
        source_document_id=str(extraction.get("sourceDocumentId") or ""),
        source_sha256=str(extraction.get("sourceSha256") or ""),
    )
    if not batch.is_usable:
        return _activity_outcome(batch)
    return StepOutcome.succeeded(data=batch.results["classification"])


def _classify_job_requirements_persist(db, context, outcome) -> dict:
    """分类大结果作为 Artifact 登记；发布阶段不再访问对象存储。"""
    return WorkflowArtifactStore().persist_outcome_json(
        db,
        workflow_run_id=context.workflow_run_id,
        artifact_type="job_requirement_classification",
        outcome=outcome,
    )


def _apply_manual_header_mapping(workbook, repair_context: dict) -> None:
    """把用户确认的列号转换为规则提取器可识别的标准表头。"""
    sheet_name = str(repair_context.get("sheetName") or "")
    row_index = repair_context.get("headerRowIndex")
    mapping = repair_context.get("headerMapping")
    sheets = {sheet.name: sheet for sheet in workbook.sheets}
    sheet = sheets.get(sheet_name)
    if sheet is None or isinstance(row_index, bool) or not isinstance(row_index, int) or not isinstance(mapping, dict):
        raise RuntimeError("job_header_mapping_invalid")
    if not (0 <= row_index < len(sheet.rows)):
        raise RuntimeError("job_header_mapping_invalid")
    used: set[int] = set()
    for field, column in mapping.items():
        if field not in {"sequence", "department", "title", "headcount", "responsibilities", "qualifications", "education", "major"}:
            raise RuntimeError("job_header_mapping_invalid")
        if isinstance(column, bool) or not isinstance(column, int) or not (0 <= column < len(sheet.rows[row_index])) or column in used:
            raise RuntimeError("job_header_mapping_invalid")
        used.add(column)
        sheet.rows[row_index][column] = HEADER_ALIASES[field][0]
    if "title" not in mapping:
        raise RuntimeError("job_header_mapping_incomplete")


def _apply_manual_field_repairs(jobs, fields: dict) -> None:
    """按源行引用覆盖字段；所有值仍会经发布前 DTO/业务校验。"""
    allowed = {"title", "headcount", "responsibilities", "qualifications", "education_requirement", "major_requirement", "department_name"}
    by_row = {}
    for job in jobs:
        metadata = job.metadata or {}
        row_ref = str(next(iter(dict(metadata.get("source_cells") or {})), ""))
        by_row[row_ref] = job
    for row_ref, values in fields.items():
        job = by_row.get(str(row_ref))
        if job is None or not isinstance(values, dict):
            raise RuntimeError("job_field_repair_row_invalid")
        for field, value in values.items():
            if field not in allowed:
                raise RuntimeError("job_field_repair_field_invalid")
            if field == "department_name":
                text = str(value or "").strip()
                if not text:
                    raise RuntimeError("job_field_repair_value_invalid")
                job.department_name = text
            elif field == "headcount":
                if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100000:
                    raise RuntimeError("job_field_repair_value_invalid")
                job.headcount = value
            elif field in {"responsibilities", "qualifications"}:
                if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
                    raise RuntimeError("job_field_repair_value_invalid")
                setattr(job, field, [item.strip() for item in value if item.strip()][:50])
            else:
                if not isinstance(value, str) or not value.strip():
                    raise RuntimeError("job_field_repair_value_invalid")
                setattr(job, field, value.strip())
            state_field = "department_id" if field == "department_name" else field
            states = dict((job.metadata.get("extraction_meta") or {}).get("fields") or {})
            if state_field in states:
                states[state_field] = {
                    **dict(states.get(state_field) or {}),
                    "origin": "user_repair",
                    "message": "用户已确认",
                }
                job.metadata["extraction_meta"] = {"fields": states}
        job.source_text = JobDocumentProcessor.build_source_text(job.department_name, job.title, job.responsibilities, job.qualifications, job.education_requirement, job.major_requirement)


def _extract_persist(db, context, outcome) -> dict:
    """短事务：保存可恢复提取结果、原文块索引及文档的当前解析状态。"""
    payload = dict(outcome.data or {})
    run = db.get(WorkflowRun, context.workflow_run_id)
    import_task = db.get(JobDocumentImport, run.subject_id) if run and run.subject_id else None
    document = db.get(SourceDocument, import_task.source_document_id) if import_task else None
    if import_task is None or document is None:
        raise RuntimeError("job_document_missing")
    parsed_text = str(payload.get("parsedText") or "")
    blocks_ref = str(payload.get("documentBlocksRef") or "")
    blocks_sha256 = str(payload.get("documentBlocksSha256") or "")
    blocks_schema_version = str(payload.get("documentBlocksSchemaVersion") or "")
    if not blocks_ref or not blocks_sha256 or not blocks_schema_version:
        raise RuntimeError("job_document_blocks_stage_missing")
    JobDocumentImportProcessService.start(import_task)
    document.parsed_text = parsed_text
    document.parser_provider = str(payload.get("parserProvider") or "unknown")
    document.document_blocks_ref = blocks_ref
    document.document_blocks_sha256 = blocks_sha256
    document.document_blocks_schema_version = blocks_schema_version
    return WorkflowArtifactStore().persist_outcome_json(
        db, workflow_run_id=context.workflow_run_id,
        artifact_type="job_document_extraction", outcome=outcome,
    )


def _publish_handler(context) -> StepOutcome:
    """事务外读取解析和分类工件；短事务只创建 JobDraft。"""
    refs = context.previous_output_refs.get("extract_job_document") or {}
    classification_refs = context.previous_output_refs.get(
        "classify_job_requirements_for_review"
    ) or {}
    with SessionLocal() as db:
        payload = WorkflowArtifactStore().get_json(db, str(refs.get("artifactId") or ""))
        classification_id = str(classification_refs.get("artifactId") or "")
        classification = (
            WorkflowArtifactStore().get_json(db, classification_id)
            if classification_id
            else {"rows": [], "degraded": True}
        )
    return StepOutcome.succeeded(data={"extraction": payload, "classification": classification})


def _publish_persist(db, context, _outcome) -> dict:
    payload = dict((_outcome.data or {}).get("extraction") or {})
    classification = dict((_outcome.data or {}).get("classification") or {})
    classification_by_row = {
        str(item.get("job_key")): item
        for item in classification.get("rows") or []
    }
    run = db.get(WorkflowRun, context.workflow_run_id)
    import_task = db.get(JobDocumentImport, run.subject_id) if run and run.subject_id else None
    document = db.get(SourceDocument, import_task.source_document_id) if import_task else None
    if run is None or run.status != "running" or run.lease_owner != context.worker_id or import_task is None or document is None:
        raise RuntimeError("workflow_lease_lost")
    department_index: dict[str, set[str]] = {}
    for department in db.scalars(select(Department).where(Department.deleted_at.is_(None))):
        department_index.setdefault(
            _normalize_department_name(department.name), set()
        ).add(department.department_id)
    db.execute(delete(JobDraft).where(JobDraft.source_document_id == document.source_document_id, JobDraft.status != "confirmed"))
    for sequence_no, item in enumerate(payload.get("jobs") or [], start=1):
        name = _normalize_department_name(str(item.get("department_name") or ""))
        ids = department_index.get(name, set())
        department_id = next(iter(ids)) if len(ids) == 1 else None
        match = "matched" if department_id else "ambiguous" if len(ids) > 1 else "auto_create" if name else "unmatched"
        recommendation = recommend_preset_model(str(item.get("title") or ""), str(item.get("source_text") or ""))
        metadata = dict(item.get("metadata") or {})
        source_cells = dict(metadata.get("source_cells") or {})
        row_ref = str(next(iter(source_cells), f"row_{sequence_no}"))
        classified = classification_by_row.get(row_ref) or {}
        db.add(JobDraft(
            job_draft_id=_id("JDRAFT"),
            source_document_id=document.source_document_id,
            sequence_no=sequence_no,
            title=str(item.get("title") or "")[:128],
            headcount=item.get("headcount"),
            responsibilities=_required_text_list(item.get("responsibilities"), "responsibilities"),
            qualifications=_required_text_list(item.get("qualifications"), "qualifications"),
            major_requirement=str(item.get("major_requirement") or "")[:500] or None,
            education_requirement=str(item.get("education_requirement") or "")[:255] or None,
            department_id=department_id,
            source_department_name=str(item.get("department_name") or "")[:255] or None,
            department_match_status=match,
            source_text=str(item.get("source_text") or ""),
            status="draft",
            confirmed_job_id=None,
            preset_model_id=str(recommendation["preset_model_id"]),
            preset_model_version=str(recommendation["preset_model_version"]),
            recommended_preset_model_id=str(recommendation["preset_model_id"]),
            preset_model_selection_source=str(recommendation.get("selection_source") or "rule"),
            major_requirement_source=str(metadata.get("major_source") or "") or None,
            major_requirement_source_quote=str(metadata.get("major_source_quote") or "") or None,
            field_provenance_json=dict(metadata.get("extraction_meta") or {"fields": {}}),
            requirement_classification_json=dict(
                classified.get("classification") or {}
            ),
            department_auto_created=False,
            created_at=_now(),
            updated_at=_now(),
        ))
    JobDocumentImportProcessService.require_review(
        import_task,
        review_kind="job_drafts",
        reason="岗位草稿已生成，请确认后发布正式岗位",
        recovery_code=JobRecoveryCode.DRAFT_REVIEW_REQUIRED.value,
        recovery_context={"jobDraftCount": len(payload.get("jobs") or [])},
    )
    # 提取/修复诊断已由 job_document_extraction Artifact 保存，不能写入文件资产。
    return {"sourceDocumentId": document.source_document_id, "jobDraftCount": str(len(payload.get("jobs") or []))}


def build_job_document_import_spec(transition_handler, blocked_handler=None, *, definition_version: int = 1, include_requirement_classification: bool = False) -> WorkflowSpec:
    """声明岗位文档导入的恢复步骤。

    v1 保留原有两步拓扑，仅用于恢复升级前已入队的任务；v2 的新任务依次执行：
    1. 提取岗位文档，形成可恢复的结构化工件；
    2. 按预算批量分类任职要求，失败时按岗位做确定性降级；
    3. 在短事务中发布 JobDraft，交给人工确认岗位及硬筛条件。
    """
    return WorkflowSpec(
        workflow_type=WORKFLOW_TYPE,
        handler=None,
        transition_handler=transition_handler,
        blocked_handler=blocked_handler,
        definition_version=definition_version,
        steps=(
            # 步骤 1：读取 Excel、修复表头并提取岗位草稿；结果先保存为工件。
            StepDefinition("extract_job_document", 1, _extract_handler, _input, StepPolicy(timeout_seconds=180, max_attempts=3, total_deadline_seconds=900, external=True), _extract_persist, artifact_type="job_document_extraction"),
            *(() if not include_requirement_classification else (
                StepDefinition(
                    "classify_job_requirements_for_review",
                    2,
                    _classify_job_requirements_handler,
                    _classification_input,
                    StepPolicy(timeout_seconds=180, max_attempts=2, total_deadline_seconds=900, initial_backoff_seconds=10, max_backoff_seconds=90, external=True),
                    _classify_job_requirements_persist,
                    artifact_type="job_requirement_classification",
                ),
            )),
            # 最后才创建 JobDraft；用户确认页读取已持久化的分类结果。
            StepDefinition("publish_job_drafts", 3 if include_requirement_classification else 2, _publish_handler, _classification_input if include_requirement_classification else _input, StepPolicy(timeout_seconds=60, max_attempts=2, total_deadline_seconds=300), _publish_persist),
        ),
    )
