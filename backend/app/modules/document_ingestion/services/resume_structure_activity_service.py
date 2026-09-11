"""简历结构化的活动级恢复与降级编排。

该服务不更新 Candidate、ResumeSubmission 或 ResumeProfile；它只将结构化阶段的外部调用
拆为可恢复 Activity，并返回既有结构化 JSON 合同。单项目 WorkUnit 的 LLM 最终失败时，
只允许转为标准 WorkUnit 的保守结果，禁止创造平行的原始证据模型。
"""
from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
from typing import Any

from backend.app.infrastructure.workflow_runtime import (
    ActivityBatchResult,
    ActivityDefinition,
    ActivityPolicy,
    ActivityResolution,
    ActivityRunner,
    is_safe_model_degradation_error,
)
from backend.app.modules.document_ingestion.processors import ResumeDocumentProcessor
from backend.app.modules.document_ingestion.processors.resume_document_processor import (
    _is_work_experience_source_refs,
)
from backend.app.shared.workflows import (
    ActivityExhaustionPolicy,
    ActivityOutcomeKind,
    StepContext,
)
from recruitment_ai_core.resume_structuring.activity_pipeline import (
    apply_work_units,
    build_manual_correction_structure,
    build_structure_result,
    degrade_project_work_units,
    degrade_skills,
    extract_project_work_units,
    extract_skills,
    prepare_structure,
    repair_outline,
    work_unit_inputs,
)


STRUCTURE_ACTIVITY_INPUT_VERSION = "resume_structure_activity_input_v4"


class ResumeStructureActivityService:
    """围绕一份已解析简历执行可恢复结构化，不包含视觉解析兜底决策。"""

    def __init__(self, runner: ActivityRunner | None = None) -> None:
        self.runner = runner or ActivityRunner()

    def structure(
        self,
        context: StepContext,
        *,
        candidate_id: str,
        resume_text: str,
        document_blocks: list[dict[str, Any]],
        name_override: str | None,
        manual_correction: dict[str, Any] | None = None,
    ) -> ActivityBatchResult:
        """依次恢复元数据、轮廓修复、逐项目 WorkUnit 与技能声明。

        基础信息与轮廓缺失可靠结果时转为待确认；单项目 WorkUnit 与技能声明在重试
        耗尽后均可按既有输出合同保守降级，不会终止整份简历。
        """
        if manual_correction is not None:
            return self._structure_manual_correction(
                candidate_id=candidate_id,
                resume_text=resume_text,
                document_blocks=document_blocks,
                name_override=name_override,
                manual_correction=manual_correction,
            )

        prepared = prepare_structure(
            candidate_id=candidate_id, resume_text=resume_text, document_blocks=document_blocks
        )
        source_hash = sha256(resume_text.encode("utf-8")).hexdigest()
        metadata_blocks = list(prepared["blocks"])

        source_available = bool(resume_text.strip())

        def metadata_fallback(_activity, error: Exception) -> ActivityResolution:
            metadata = _extract_metadata_deterministic(
                resume_text, name_override, logical_blocks=metadata_blocks
            )
            return ActivityResolution(
                outcome_kind=ActivityOutcomeKind.DEGRADED,
                payload=metadata,
                resolution_code="resume_metadata_deterministic_fallback",
                quality_summary={"usable": True, "degraded": True, "source": "deterministic", "errorType": type(error).__name__},
            )

        def metadata_blocked(_activity, _error: Exception) -> ActivityResolution:
            return ActivityResolution(
                outcome_kind=ActivityOutcomeKind.BLOCKED,
                payload={},
                resolution_code="resume_metadata_review_required",
                quality_summary={"usable": False, "requiredAction": "confirm_basic_info"},
            )

        metadata_batch = self.runner.run_many(
            workflow_run_id=context.workflow_run_id, worker_id=context.worker_id,
            parent_step_name=context.step_name,
            activities=[ActivityDefinition(
                activity_key="resume_metadata",
                input_data={
                    "resumeSha256": source_hash,
                    "nameOverride": name_override or "",
                    "contractVersion": STRUCTURE_ACTIVITY_INPUT_VERSION,
                },
                handler=lambda _activity: _extract_metadata(
                    resume_text,
                    name_override,
                    logical_blocks=metadata_blocks,
                ),
                policy=ActivityPolicy(max_attempts=2, retry_after_seconds=15),
                exhaustion_policy=(
                    ActivityExhaustionPolicy.FALLBACK_TO_STANDARD_MODEL
                    if source_available else ActivityExhaustionPolicy.BLOCK_FOR_USER_ACTION
                ),
                on_exhausted=metadata_fallback if source_available else metadata_blocked,
                can_degrade=is_safe_model_degradation_error,
            )],
        )
        if not metadata_batch.is_usable:
            return metadata_batch

        outline_batch = None
        if not bool(dict(prepared["validation"]).get("accepted")):
            resume_ir = dict(prepared.get("resumeIr") or {})
            has_outline_skeleton = bool(prepared.get("blocks")) and bool(
                resume_ir.get("experience_units") or resume_ir.get("source_bullets")
            )

            def outline_fallback(_activity, error: Exception) -> ActivityResolution:
                # 规则骨架仍可用时保留它，只把校验错误转成结构告警，避免丢弃整份简历。
                validation = dict(prepared.get("validation") or {})
                validation["accepted"] = True
                validation["warnings"] = [
                    *list(validation.get("warnings") or []),
                    "outline_repair_degraded_deterministic_skeleton_kept",
                ]
                payload = {
                    **prepared,
                    "validation": validation,
                    "outlineRepaired": True,
                    "traces": [
                        *list(prepared.get("traces") or []),
                        {"outcome": "degraded", "resolution_code": "resume_outline_deterministic_fallback", "error_type": type(error).__name__},
                    ],
                }
                return ActivityResolution(
                    outcome_kind=ActivityOutcomeKind.DEGRADED,
                    payload=payload,
                    resolution_code="resume_outline_deterministic_fallback",
                    quality_summary={"usable": True, "degraded": True, "source": "deterministic_skeleton", "errorType": type(error).__name__},
                )

            def outline_blocked(_activity, _error: Exception) -> ActivityResolution:
                return ActivityResolution(
                    outcome_kind=ActivityOutcomeKind.BLOCKED,
                    payload={},
                    resolution_code="resume_outline_review_required",
                    quality_summary={"usable": False, "requiredAction": "correct_parsed_resume"},
                )

            outline_batch = self.runner.run_many(
                workflow_run_id=context.workflow_run_id, worker_id=context.worker_id,
                parent_step_name=context.step_name,
                activities=[ActivityDefinition(
                    activity_key="outline_repair",
                    input_data={
                        "resumeSha256": source_hash,
                        "validation": dict(prepared["validation"]),
                        "resumeIr": dict(prepared.get("resumeIr") or {}),
                        "contractVersion": STRUCTURE_ACTIVITY_INPUT_VERSION,
                    },
                    handler=lambda _activity: repair_outline(prepared),
                    policy=ActivityPolicy(max_attempts=2, retry_after_seconds=15),
                    exhaustion_policy=(
                        ActivityExhaustionPolicy.FALLBACK_TO_STANDARD_MODEL
                        if has_outline_skeleton else ActivityExhaustionPolicy.BLOCK_FOR_USER_ACTION
                    ),
                    on_exhausted=outline_fallback if has_outline_skeleton else outline_blocked,
                    can_degrade=is_safe_model_degradation_error,
                )],
            )
            if not outline_batch.is_usable:
                return outline_batch
            prepared = dict(outline_batch.results["outline_repair"])

        project_inputs = work_unit_inputs(prepared)
        project_definitions: list[ActivityDefinition] = []
        for project in project_inputs:
            project_id = str(project["project_id"])

            def project_fallback(
                _activity,
                error: Exception,
                *,
                failed_project_id: str = project_id,
                failed_project: dict[str, Any] = project,
            ) -> ActivityResolution:
                payload = degrade_project_work_units(failed_project, error)
                preserved_count = len(list(payload.get("workUnits") or []))
                return ActivityResolution(
                    outcome_kind=ActivityOutcomeKind.DEGRADED,
                    payload=payload,
                    resolution_code="work_unit_source_bullets_preserved",
                    quality_summary={
                        "usable": True,
                        "degraded": True,
                        "projectId": failed_project_id,
                        "preservedWorkUnitCount": preserved_count,
                        "recommendedAction": "correct_parsed_resume",
                        "errorType": type(error).__name__,
                    },
                )

            project_definitions.append(ActivityDefinition(
                activity_key=f"work_unit:{project_id}",
                input_data={
                    "resumeSha256": source_hash,
                    "project": project,
                    "contractVersion": STRUCTURE_ACTIVITY_INPUT_VERSION,
                },
                handler=lambda _activity, current=project: extract_project_work_units(current),
                policy=ActivityPolicy(max_attempts=2, retry_after_seconds=15),
                exhaustion_policy=ActivityExhaustionPolicy.FALLBACK_TO_STANDARD_MODEL,
                on_exhausted=project_fallback,
                can_degrade=is_safe_model_degradation_error,
            ))

        project_batch = self.runner.run_many(
            workflow_run_id=context.workflow_run_id,
            worker_id=context.worker_id,
            parent_step_name=context.step_name,
            activities=project_definitions,
        )
        if not project_batch.is_usable:
            return project_batch
        prepared = apply_work_units(
            prepared,
            [
                project_batch.results[definition.activity_key]
                for definition in project_definitions
            ],
        )
        prepared = {
            **prepared,
            "activityQuality": {
                "metadata": {
                    "outcomeKinds": {
                        **dict(metadata_batch.outcome_kinds),
                        **(
                            {"resume_metadata": "degraded"}
                            if str(
                                dict(
                                    metadata_batch.results["resume_metadata"].get("trace")
                                    or {}
                                ).get("mode")
                                or ""
                            )
                            in {"llm_partial", "deterministic_fallback"}
                            else {}
                        ),
                    },
                    "summaries": {
                        **dict(metadata_batch.quality_summaries),
                        "resume_metadata": {
                            "missingFields": list(
                                dict(
                                    metadata_batch.results["resume_metadata"]["metadata"].get("metadata")
                                    or {}
                                ).get("dropped_invalid_fields")
                                or []
                            )
                        },
                    },
                },
                "outline": {
                    "outcomeKinds": dict(outline_batch.outcome_kinds) if outline_batch else {},
                    "summaries": dict(outline_batch.quality_summaries) if outline_batch else {},
                },
                "workUnits": {
                    "outcomeKinds": dict(project_batch.outcome_kinds),
                    "summaries": dict(project_batch.quality_summaries),
                },
            },
        }
        skill_batch = self.runner.run_many(
            workflow_run_id=context.workflow_run_id, worker_id=context.worker_id,
            parent_step_name=context.step_name,
            activities=[ActivityDefinition(
                activity_key="skill_claims",
                input_data={
                    "resumeSha256": source_hash,
                    "resumeIr": prepared["resumeIr"],
                    "contractVersion": STRUCTURE_ACTIVITY_INPUT_VERSION,
                },
                handler=lambda _activity: extract_skills(prepared),
                policy=ActivityPolicy(max_attempts=2, retry_after_seconds=15),
                # 技能模型失败不等于候选人没有技能。保留已经通过 section 与原文
                # 约束筛出的声明，后续评分可识别其降级质量，用户也能据原文校正。
                exhaustion_policy=ActivityExhaustionPolicy.FALLBACK_TO_STANDARD_MODEL,
                on_exhausted=lambda _activity, error: ActivityResolution(
                    outcome_kind=ActivityOutcomeKind.DEGRADED,
                    payload=degrade_skills(prepared, error),
                    resolution_code="skill_claim_source_text_preserved",
                    quality_summary={
                        "usable": True,
                        "degraded": True,
                        "source": "verified_skill_source_text",
                    },
                ),
                can_degrade=is_safe_model_degradation_error,
            )],
        )
        if not skill_batch.is_usable:
            return skill_batch
        prepared = {
            **prepared,
            "activityQuality": {
                **dict(prepared.get("activityQuality") or {}),
                "skillClaims": {
                    "outcomeKinds": dict(skill_batch.outcome_kinds),
                    "summaries": dict(skill_batch.quality_summaries),
                },
            },
        }
        structure = build_structure_result(
            prepared,
            skill_batch.results["skill_claims"],
            candidate_facts=_candidate_facts(
                dict(metadata_batch.results["resume_metadata"]["metadata"])
            ),
            manual_correction=manual_correction,
        )
        metadata = metadata_batch.results["resume_metadata"]
        overall_status = "degraded" if "degraded" in {
            metadata_batch.status,
            outline_batch.status if outline_batch else "completed",
            project_batch.status,
            skill_batch.status,
        } else "completed"
        return ActivityBatchResult(
            status=overall_status,
            results={
                "structure": structure,
                "metadata": dict(metadata["metadata"]),
                "trace": {
                    **dict(metadata["trace"]),
                    "activity_quality": dict(prepared.get("activityQuality") or {}),
                },
            },
            outcome_kinds={
                **dict(metadata_batch.outcome_kinds),
                **(dict(outline_batch.outcome_kinds) if outline_batch else {}),
                **dict(project_batch.outcome_kinds),
                **dict(skill_batch.outcome_kinds),
            },
            quality_summaries={
                **dict(metadata_batch.quality_summaries),
                **(dict(outline_batch.quality_summaries) if outline_batch else {}),
                **dict(project_batch.quality_summaries),
                **dict(skill_batch.quality_summaries),
            },
        )

    @staticmethod
    def _structure_manual_correction(
        *,
        candidate_id: str,
        resume_text: str,
        document_blocks: list[dict[str, Any]],
        name_override: str | None,
        manual_correction: dict[str, Any],
    ) -> ActivityBatchResult:
        """Publish user-confirmed source selections without re-entering any LLM path."""
        metadata_result = _extract_metadata_deterministic(resume_text, name_override)
        metadata = dict(metadata_result["metadata"])
        structure = build_manual_correction_structure(
            candidate_id=candidate_id,
            resume_text=resume_text,
            document_blocks=document_blocks,
            manual_correction=manual_correction,
            candidate_facts=_candidate_facts(metadata),
        )
        return ActivityBatchResult(
            status="completed",
            results={
                "structure": structure,
                "metadata": metadata,
                "trace": {
                    **dict(metadata_result.get("trace") or {}),
                    "mode": "manual_correction_llm_bypassed",
                },
            },
            outcome_kinds={"manual_correction": "user_confirmed"},
            quality_summaries={
                "manual_correction": {
                    "usable": True,
                    "source": "immutable_blocks",
                    "llm_bypassed": True,
                }
            },
        )


def _extract_metadata(
    text: str,
    name_override: str | None,
    *,
    logical_blocks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """一次基础信息 LLM 调用的活动处理器；只返回 JSON，不写领域对象。"""
    metadata, trace = ResumeDocumentProcessor().extract(
        text,
        candidate_name_override=name_override,
        logical_blocks=logical_blocks,
    )
    return {"metadata": asdict(metadata), "trace": trace}


def _extract_metadata_deterministic(
    text: str,
    name_override: str | None,
    *,
    logical_blocks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """元数据 LLM 耗尽后的本地确定性回退，保持同一输出合同。"""
    metadata = ResumeDocumentProcessor().extract_deterministic(
        text, logical_blocks=logical_blocks
    )
    if name_override and name_override.strip():
        metadata.candidate_name = name_override.strip()
        metadata.metadata = {**dict(metadata.metadata or {}), "candidate_name_source": "upload_override"}
    return {
        "metadata": asdict(metadata),
        "trace": {"mode": "deterministic_activity_fallback"},
    }


def _candidate_facts(metadata: dict[str, Any]) -> dict[str, Any]:
    """Publish only LLM/user facts already bound to immutable source Blocks."""
    field_refs = dict((metadata.get("metadata") or {}).get("field_source_refs") or {})

    def clean_refs(value: Any) -> list[dict[str, str]]:
        return [
            {
                "block_id": str(item.get("block_id") or ""),
                "quote": str(item.get("quote") or "").strip(),
            }
            for item in list(value or [])
            if isinstance(item, dict)
            and str(item.get("block_id") or "")
            and str(item.get("quote") or "").strip()
        ]

    records: list[dict[str, Any]] = []
    for raw in list(metadata.get("education_records") or []):
        if not isinstance(raw, dict):
            continue
        education_refs = clean_refs(raw.get("source_refs"))
        # 教育事实是硬筛输入，必须仍能追溯到不可变逻辑 Block；无来源的
        # LLM 记录只作为活动告警保留在 trace 中，不能悄悄进入算法。
        if not education_refs:
            continue
        records.append({
            "school": str(raw.get("school") or "").strip() or None,
            "major": str(raw.get("major") or "").strip() or None,
            "degree_level": _degree_level(raw.get("degree")),
            "status": str(raw.get("status") or "unknown"),
            "start_year": raw.get("start_year") if isinstance(raw.get("start_year"), int) else None,
            "graduation_year": raw.get("graduation_year") if isinstance(raw.get("graduation_year"), int) else None,
            "raw_text": "\n".join(item["quote"] for item in education_refs),
            "source_refs": education_refs,
        })
    experience_value = metadata.get("relevant_experience_years")
    experience_refs = clean_refs(field_refs.get("relevant_experience_years"))
    if (
        not isinstance(experience_value, (int, float))
        or isinstance(experience_value, bool)
        or not experience_refs
        or not _is_work_experience_source_refs(experience_refs)
    ):
        experience_value = None
        experience_refs = []
    qualification_records: list[dict[str, Any]] = []
    for raw in list(metadata.get("qualification_records") or []):
        if not isinstance(raw, dict):
            continue
        source_refs = clean_refs(raw.get("source_refs"))
        if not source_refs:
            continue
        qualification_records.append({
            "raw_text": "；".join(item["quote"] for item in source_refs),
            "source_refs": source_refs,
        })
    return {
        "education_records": records,
        "relevant_experience_years": {
            "value": float(experience_value) if isinstance(experience_value, (int, float)) else None,
            "source_refs": experience_refs,
        },
        "qualification_records": qualification_records,
    }


def _degree_level(value: Any) -> str:
    text = str(value or "").strip().casefold()
    if "博士" in text or "doctor" in text or "phd" in text:
        return "doctorate"
    if "硕士" in text or "研究生" in text or "master" in text:
        return "master"
    if "本科" in text or "学士" in text or "bachelor" in text:
        return "bachelor"
    if "大专" in text or "专科" in text or "associate" in text:
        return "associate"
    return "other"
