"""岗位 Excel 导入的活动级修复服务。

规则提取始终是主路径；只有表头或单条岗位字段被标记为 unresolved 时，才创建对应
LLM 活动。活动产物是可重新应用的修复计划，不依赖进程内 Workbook 或 ORM 对象。
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from backend.app.infrastructure.workflow_runtime import (
    ActivityBatchResult,
    ActivityDefinition,
    ActivityPolicy,
    ActivityResolution,
    ActivityRunner,
    is_safe_model_degradation_error,
)
from backend.app.modules.document_ingestion.parsing import ExcelWorkbook
from backend.app.modules.document_ingestion.processors import (
    ExtractedJob,
    JobExtractionRepairService,
    JobHeaderRepairService,
)
from backend.app.modules.document_ingestion.processors.job_document_processor import JobDocumentProcessor
from backend.app.shared.workflows import ActivityExhaustionPolicy, ActivityOutcomeKind, StepContext


class JobDocumentActivityService:
    """仅编排可独立恢复的岗位文档 LLM 修复，不负责发布 JobDraft。"""

    def __init__(self, runner: ActivityRunner | None = None) -> None:
        self.runner = runner or ActivityRunner()

    def repair_headers(
        self,
        context: StepContext,
        *,
        workbook: ExcelWorkbook,
        source_document_id: str,
        source_sha256: str,
        preserve_existing: bool = False,
    ) -> ActivityBatchResult:
        """表头无法由规则识别时执行一次活动；成功结果可应用到重读的 Workbook。"""
        def fallback(_activity, error: Exception) -> ActivityResolution:
            return ActivityResolution(
                outcome_kind=ActivityOutcomeKind.DEGRADED,
                payload={"repair_plans": [], "diagnostics": {
                    "outcome": "degraded",
                    "resolutionCode": "job_document_header_kept_rule_result",
                    "errorType": type(error).__name__,
                }},
                resolution_code="job_document_header_kept_rule_result",
                quality_summary={"usable": True, "degraded": True, "preservedExisting": preserve_existing},
            )

        def blocked(_activity, _error: Exception) -> ActivityResolution:
            return ActivityResolution(
                outcome_kind=ActivityOutcomeKind.BLOCKED,
                payload={},
                resolution_code="job_document_header_review_required",
                quality_summary={"usable": False, "requiredAction": "confirm_job_document_header"},
            )

        definition = ActivityDefinition(
            activity_key="header_repair",
            input_data={
                "sourceDocumentId": source_document_id,
                "sourceSha256": source_sha256,
                "sheets": [sheet.name for sheet in workbook.sheets],
            },
            handler=lambda _activity: JobHeaderRepairService().repair(workbook),
            policy=ActivityPolicy(max_attempts=2, retry_after_seconds=15),
            exhaustion_policy=(
                ActivityExhaustionPolicy.FALLBACK_TO_STANDARD_MODEL
                if preserve_existing else ActivityExhaustionPolicy.BLOCK_FOR_USER_ACTION
            ),
            on_exhausted=fallback if preserve_existing else blocked,
            can_degrade=is_safe_model_degradation_error,
        )
        return self.runner.run_many(
            workflow_run_id=context.workflow_run_id,
            worker_id=context.worker_id,
            parent_step_name=context.step_name,
            activities=[definition],
        )

    def repair_job_fields(
        self,
        context: StepContext,
        *,
        jobs: list[ExtractedJob],
        source_document_id: str,
        source_sha256: str,
    ) -> ActivityBatchResult:
        """每一条含 unresolved 字段的岗位草稿对应一个活动；正常行不会触发 LLM。"""
        activities: list[ActivityDefinition] = []
        for index, job in enumerate(jobs, start=1):
            states = dict((job.metadata.get("extraction_meta") or {}).get("fields") or {})
            pending = [
                key for key, state in states.items()
                if isinstance(state, dict) and state.get("origin") == "unresolved"
            ]
            if not pending:
                continue
            row_ref = str(next(iter(dict(job.metadata.get("source_cells") or {})), f"row_{index}"))
            activities.append(ActivityDefinition(
                activity_key=f"field_repair:{row_ref}",
                input_data={
                    "sourceDocumentId": source_document_id,
                    "sourceSha256": source_sha256,
                    "row": row_ref,
                    "pending": sorted(pending),
                    "job": asdict(job),
                },
                handler=lambda _activity, job=job: JobExtractionRepairService().repair(job),
                policy=ActivityPolicy(max_attempts=2, retry_after_seconds=15),
                # 单行字段修复失败时保留规则已提取的标准 ExtractedJob 与来源字段，
                # 由 JobDraft 页面标记为待确认；不能因此丢掉整份 Excel 的其他岗位。
                exhaustion_policy=ActivityExhaustionPolicy.FALLBACK_TO_STANDARD_MODEL,
                on_exhausted=lambda _activity, error, job=job, row_ref=row_ref: ActivityResolution(
                    outcome_kind=ActivityOutcomeKind.DEGRADED,
                    payload={
                        "job": asdict(job),
                        "diagnostics": {
                            "outcome": "degraded",
                            "resolutionCode": "job_field_repair_kept_rule_result",
                            "row": row_ref,
                            "errorType": type(error).__name__,
                        },
                    },
                    resolution_code="job_field_repair_kept_rule_result",
                    quality_summary={"usable": True, "degraded": True, "row": row_ref},
                ),
                can_degrade=is_safe_model_degradation_error,
            ))
        if not activities:
            return ActivityBatchResult(status="completed", results={})
        return self.runner.run_many(
            workflow_run_id=context.workflow_run_id,
            worker_id=context.worker_id,
            parent_step_name=context.step_name,
            activities=activities,
        )

    @staticmethod
    def apply_field_repairs(jobs: list[ExtractedJob], results: dict[str, dict[str, Any]]) -> list[ExtractedJob]:
        """用活动 Artifact 中的完整草稿替换内存草稿；恢复路径不再次调用模型。"""
        by_row = {
            f"field_repair:{str(next(iter(dict(job.metadata.get('source_cells') or {})), f'row_{index}'))}": index - 1
            for index, job in enumerate(jobs, start=1)
        }
        repaired = list(jobs)
        for activity_key, result in results.items():
            index = by_row.get(activity_key)
            payload = result.get("job") if isinstance(result, dict) else None
            if index is not None and isinstance(payload, dict):
                job = ExtractedJob(**payload)
                job.source_text = JobDocumentProcessor.build_source_text(
                    job.department_name,
                    job.title,
                    job.responsibilities,
                    job.qualifications,
                    job.education_requirement,
                    job.major_requirement,
                )
                repaired[index] = job
        return repaired
