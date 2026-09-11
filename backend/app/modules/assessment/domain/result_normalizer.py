"""评估版本结果的唯一归一化适配层。

V1 初筛算法和 V2/V3 增量算法使用不同的内存 dataclass，但它们发布到
``ApplicationAssessmentVersion`` 前必须收敛为同一份领域合同。该模块只做字段
兼容、缺省值补齐和 Schema 校验，绝不重算分数或生成新的业务结论。
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any, Mapping

from backend.app.modules.assessment.domain.assessment_version import (
    AssessmentCoreResult,
    AssessmentRuleResult,
    AssessmentSignal,
    AssessmentStage,
    InterviewTargetStatus,
    InterviewTargetUpdate,
)


def normalize_assessment_core_result(value: Any) -> dict[str, Any]:
    """将 V1 ``preset_experience_result`` 或 V2/V3 结果收敛成统一核心结果。"""
    raw = _mapping(value)
    score = _score_result(raw.get("score_result") or raw.get("scoreResult"))
    capability_graph = _mapping(raw.get("capability_graph") or raw.get("capabilityGraph"))
    # V1 的 evidence_index 仍是可追溯核心事实，放入能力图谱命名区块，避免在顶层
    # 保留一套只属于 V1 的字段。
    if "evidence_index" in raw and "evidence_index" not in capability_graph:
        capability_graph["evidence_index"] = _mapping(raw.get("evidence_index"))
    normalized = AssessmentCoreResult(
        experience_result=_mapping(
            raw.get("experience_result")
            or raw.get("preset_experience_result")
            or raw.get("presetExperienceResult")
        ),
        job_result=_mapping(raw.get("job_result") or raw.get("jobResult")),
        education_result=_mapping(raw.get("education_result") or raw.get("educationResult")),
        score_result=score,
        capability_graph=capability_graph,
        affected_result_refs=_strings(raw.get("affected_result_refs") or raw.get("affectedResultRefs")),
        score_changes=_list_of_mappings(raw.get("score_changes") or raw.get("scoreChanges")),
        capability_changes=_list_of_mappings(
            raw.get("capability_changes") or raw.get("capabilityChanges")
        ),
    )
    return normalized.model_dump(mode="json")


def normalize_assessment_rule_result(value: Any) -> dict[str, Any]:
    """收敛 V1 旧 Signal 字典与 V2/V3 新规则结果，保证稳定键和排名语义。"""
    raw = _mapping(value)
    strengths = _signals(raw.get("strength_signals") or raw.get("strengthSignals"), "strength")
    weaknesses = _signals(raw.get("weakness_signals") or raw.get("weaknessSignals"), "weakness")
    result = AssessmentRuleResult(
        strength_signals=strengths[:4],
        weakness_signals=weaknesses[:4],
        target_updates=_target_updates(raw.get("target_updates") or raw.get("targetUpdates")),
        signal_comparison=raw.get("signal_comparison") or raw.get("signalComparison"),
        target_comparison=raw.get("target_comparison") or raw.get("targetComparison"),
    )
    return result.model_dump(mode="json")


def _mapping(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return dict(value.model_dump(mode="json"))
    if hasattr(value, "as_dict"):
        return dict(value.as_dict())
    if hasattr(value, "__dataclass_fields__"):
        return dict(asdict(value))
    return dict(value) if isinstance(value, Mapping) else {}


def _list_of_mappings(value: Any) -> list[dict[str, Any]]:
    return [dict(item) for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []


def _strings(value: Any) -> list[str]:
    return list(dict.fromkeys(str(item) for item in value if item)) if isinstance(value, list) else []


def _score_result(value: Any) -> dict[str, Any]:
    raw = _mapping(value)
    aliases = {
        "total": ("total", "total_score", "overall_score", "base_score"),
        "job_fit": ("job_fit", "job_fit_score", "job_score", "job_capability_fit_score", "jobCapabilityFitScore"),
        "experience": ("experience", "experience_score", "preset_experience_score", "resume_experience_score", "resumeExperienceScore"),
        "education": ("education", "education_score", "education_background_score", "educationBackgroundScore"),
    }
    result: dict[str, Any] = {}
    for canonical, candidates in aliases.items():
        current = next((raw[name] for name in candidates if isinstance(raw.get(name), (int, float))), None)
        result[canonical] = float(current) if current is not None else None
    result["qualification_status"] = str(
        raw.get("qualification_status") or raw.get("qualificationStatus") or "unclear"
    )
    return result


def _signals(value: Any, kind: str) -> list[AssessmentSignal]:
    result: list[AssessmentSignal] = []
    for rank, item in enumerate(_list_of_mappings(value), start=1):
        target_id = str(
            item.get("target_id")
            or item.get("targetId")
            or item.get("job_capability_id")
            or item.get("indicator_id")
            or item.get("signal_id")
            or ""
        )
        source_type = str(
            item.get("source_type")
            or item.get("sourceType")
            or ("preset_indicator" if item.get("indicator_id") else "job_capability")
        )
        if not target_id:
            continue
        result.append(
            AssessmentSignal(
                signal_key=str(item.get("signal_key") or item.get("signalKey") or f"{kind}:{source_type}:{target_id}"),
                source_type=source_type,
                target_id=target_id,
                title=str(item.get("title") or item.get("name") or target_id),
                reason=str(item.get("reason") or item.get("summary") or ""),
                source_result_ids=_strings(item.get("source_result_ids") or item.get("sourceResultIds")),
                evidence_ids=_strings(item.get("evidence_ids") or item.get("evidenceIds")),
                current_rank=int(item.get("current_rank") or item.get("currentRank") or rank),
                previous_rank=(
                    int(item["previous_rank"])
                    if isinstance(item.get("previous_rank"), int)
                    else (int(item["previousRank"]) if isinstance(item.get("previousRank"), int) else None)
                ),
            )
        )
    return result


def _target_updates(value: Any) -> list[InterviewTargetUpdate]:
    result: list[InterviewTargetUpdate] = []
    for item in _list_of_mappings(value):
        target_id = str(item.get("interview_target_id") or item.get("interviewTargetId") or "")
        if not target_id:
            continue
        status = str(item.get("status") or InterviewTargetStatus.OPEN.value)
        stage = str(item.get("stage_created") or item.get("stageCreated") or AssessmentStage.SCREENING.value)
        try:
            result.append(
                InterviewTargetUpdate(
                    interview_target_id=target_id,
                    purpose=str(item.get("purpose") or "verify_experience"),
                    target_type=str(item.get("target_type") or item.get("targetType") or "job_capability"),
                    target_id=str(item.get("target_id") or item.get("targetId") or ""),
                    title=str(item.get("title") or item.get("target_id") or item.get("targetId") or ""),
                    verification_goal=str(item.get("verification_goal") or item.get("verificationGoal") or ""),
                    trigger_code=str(item.get("trigger_code") or item.get("triggerCode") or ""),
                    status=InterviewTargetStatus(status),
                    stage_created=AssessmentStage(stage),
                    resolved_stage=(
                        AssessmentStage(str(item.get("resolved_stage") or item.get("resolvedStage")))
                        if item.get("resolved_stage") or item.get("resolvedStage")
                        else None
                    ),
                    resolution_note=(
                        str(item.get("resolution_note") or item.get("resolutionNote"))
                        if item.get("resolution_note") or item.get("resolutionNote")
                        else None
                    ),
                    source_result_ids=_strings(item.get("source_result_ids") or item.get("sourceResultIds")),
                    evidence_ids=_strings(item.get("evidence_ids") or item.get("evidenceIds")),
                )
            )
        except ValueError:
            # 历史审计快照不保证满足新枚举；不可让无效条目偷偷进入当前正式版本。
            continue
    return result


