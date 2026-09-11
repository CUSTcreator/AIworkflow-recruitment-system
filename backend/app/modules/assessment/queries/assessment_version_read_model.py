"""从正式评估版本即时组装初筛页面读模型。

这里是接口适配层，不会把 ``screeningResult`` 或前端 DTO 写回数据库。
"""
from __future__ import annotations

from typing import Any

from backend.app.models.entities import ApplicationAssessmentVersion
from backend.app.modules.assessment.queries.decision_overview import (
    build_candidate_decision_overview,
)
from backend.app.modules.assessment.queries.decision_support import build_decision_support
from recruitment_ai_core.screening_scoring.screening_view_builder import (
    build_screening_result_view,
)
from recruitment_ai_core.decision_summary.recommendation_policy import (
    compute_recommendation_level,
)
from recruitment_ai_core.decision_summary.fallback import build_summary_text

_RECOMMENDATION_LEVELS = frozenset({
    "strongly_recommend",
    "recommend",
    "cautious_recommend",
    "not_recommend",
    "strongly_not_recommend",
})


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _items(value: Any) -> list[dict[str, Any]]:
    return [dict(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _strip_raw_text(value: Any) -> None:
    """删除只允许内部追溯的原始文本，避免它进入任何页面 DTO。"""
    if isinstance(value, dict):
        value.pop("rawText", None)
        for nested in value.values():
            _strip_raw_text(nested)
    elif isinstance(value, list):
        for nested in value:
            _strip_raw_text(nested)

def screening_result_view(
    row: ApplicationAssessmentVersion,
    *,
    resume_profile: dict[str, Any] | None = None,
    job_profile: dict[str, Any] | None = None,
    hard_screening: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """用 AAV 与其显式来源画像重建初筛详情，不在查询时重新计算分数。"""
    core = _mapping(row.core_result_json)
    rule = _mapping(row.rule_result_json)
    # AAV 已按统一领域合同归一化；旧初筛详情构建器仍使用 V1 算法字段名。
    # 这里只做无损字段适配，画像内容始终通过 AAV 的外键读取，不能从旧快照兜底。
    score = _mapping(core.get("score_result"))
    legacy_score = {
        "base_score": score.get("total"),
        "job_capability_fit_score": score.get("job_fit"),
        "resume_experience_score": score.get("experience"),
        "education_background_score": score.get("education"),
        "qualification_status": score.get("qualification_status"),
    }
    candidate_profile = {
        **_mapping(core.get("capability_graph")),
        "job_result": _mapping(core.get("job_result")),
        "preset_experience_result": _mapping(core.get("experience_result")),
        "education_result": _mapping(core.get("education_result")),
    }
    result = build_screening_result_view(
        application_id=row.application_id,
        candidate_profile=candidate_profile,
        job_profile=_mapping(job_profile),
        score_output=legacy_score,
        qualification=_mapping(hard_screening),
        resume_profile=_mapping(resume_profile),
        interview_targets=_items(rule.get("target_updates")) or _items(rule.get("interview_target_drafts")),
    )
    # 兼容旧前端映射器：没有草稿时仍返回稳定的空集合，而不是省略整个对象。
    focus = _mapping(result.get("interviewFocus"))
    focus["firstInterviewFocus"] = _items(focus.get("firstInterviewFocus"))
    result["interviewFocus"] = focus
    # 原始简历文本只用于内部追溯，初筛和一面页面均不可见。
    _strip_raw_text(result)
    return result


def decision_summary(row: ApplicationAssessmentVersion) -> dict[str, Any]:
    """把展示区块转成概览构建器的输入格式；LLM 文案与规则结论仍严格分离。"""
    source = _mapping(row.source_json)
    rule = _mapping(row.rule_result_json)
    presentation = _mapping(row.presentation_json)
    score_result = _mapping(_mapping(row.core_result_json).get("score_result"))
    hard_result = _mapping(source.get("hardScreening"))
    # 新版本把推荐等级作为后端策略的冻结结果写入 AAV。读取时优先使用
    # 该结果，保证生成阶段考虑的核心能力缺口等事实不会因读模型缺少完整
    # 岗位画像而丢失。只有旧数据的 neutral/非法值才按冻结分数兜底重算。
    stored_level = str(presentation.get("recommendation_level") or "")
    computed_level = (
        stored_level
        if stored_level in _RECOMMENDATION_LEVELS
        else compute_recommendation_level(
            score_result,
            hard_screening_status=str(hard_result.get("status") or ""),
        )
    )
    summary_input = {
        "recommendation": {"level": computed_level},
        "strengths": [
            {"target_name": item.get("title"), "result_summary": item.get("summary")}
            for item in _items(presentation.get("strengths"))
        ],
        "weaknesses": [
            {"target_name": item.get("title"), "result_summary": item.get("summary")}
            for item in _items(presentation.get("weaknesses"))
        ],
        "verification_focus": [
            {
                "target_name": item.get("title"),
                "reason": item.get("reason"),
                "verification_goal": item.get("goal") or item.get("verification_goal"),
            }
            for item in _items(presentation.get("verification_focus"))
        ],
    }
    stored_reason = str(presentation.get("recommendation_reason") or "").strip()
    recommendation_reason = (
        stored_reason
        if stored_reason and len(stored_reason) <= 100 and not _is_generic_recommendation_reason(stored_reason)
        else build_summary_text(summary_input)
    )
    return {
        "summary_version": "screening_presentation_v1",
        # 该字段是页面标题的唯一事实来源；不得用所在页面或 Application 状态猜测版本。
        "stage": row.stage,
        "recommendation": {
            "level": computed_level,
            "reason": recommendation_reason,
        },
        "ai_summary": {
            "recommendation_reason": recommendation_reason,
            "strengths": _items(presentation.get("strengths")),
            "weaknesses": _items(presentation.get("weaknesses")),
            "generation_mode": presentation.get("summary_generation_mode") or presentation.get("generation_mode") or "rule_fallback",
        },
        "verification_focus": {
            "items": _items(presentation.get("verification_focus")),
            "generation_mode": presentation.get("verification_focus_generation_mode") or presentation.get("generation_mode") or "rule_fallback",
        },
        "source_snapshot_hash": source.get("sourceInputHash") or "",
        "generated_at": presentation.get("generated_at") or (row.published_at or row.created_at).isoformat(),
    }


def decision_summary_view(row: ApplicationAssessmentVersion) -> dict[str, Any]:
    """将内部展示层摘要转为前端 ``DecisionSummary`` 合同。

    ``decision_summary`` 保持给业务概览构建器消费的蛇形内部结构；本函数是 HTTP DTO 的
    驼峰适配层，避免页面拿到半内部对象后自行猜字段。
    """
    internal = decision_summary(row)
    ai = _mapping(internal.get("ai_summary"))
    recommendation = _mapping(internal.get("recommendation"))
    # 读模型是边界防线：即使旧 JSON 或手工数据含有非法值，HTTP 合同仍只暴露
    # 五档推荐程度，不把模型内部或已删除的旧状态透传给前端。
    level = _public_recommendation_level(recommendation.get("level"))

    def items(values: Any) -> list[dict[str, Any]]:
        return [
            {
                "text": str(item.get("summary") or item.get("title") or ""),
                "sourceRefs": [
                    {"type": "evidence", "id": str(value)}
                    for value in item.get("evidence_ids") or [] if value
                ],
            }
            for item in _items(values)
        ]

    return {
        "summaryVersion": str(internal.get("summary_version") or "assessment_presentation_v1"),
        "stage": row.stage,
        "recommendation": {
            "level": level,
            "reason": str(recommendation.get("reason") or ai.get("recommendation_reason") or ""),
        },
        "strengths": items(ai.get("strengths")),
        "risks": items(ai.get("weaknesses")),
        "generatedAt": str(internal.get("generated_at") or ""),
        "generationMode": str(ai.get("generation_mode") or "rule_fallback"),
        "sourceSnapshotHash": str(internal.get("source_snapshot_hash") or ""),
    }


def _public_recommendation_level(value: Any) -> str:
    level = str(value or "")
    return level if level in _RECOMMENDATION_LEVELS else "cautious_recommend"


def _is_generic_recommendation_reason(value: str) -> bool:
    normalized = "".join(str(value or "").split())
    return normalized in {
        "当前材料呈现出优势与待了解事项并存的情况，建议在后续面试中围绕关注点进一步了解。",
        "优缺点并存，建议后续了解。",
    }

def assessment_payload(
    row: ApplicationAssessmentVersion,
    *,
    resume_profile: dict[str, Any] | None = None,
    job_profile: dict[str, Any] | None = None,
    hard_screening: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """提供旧查询辅助函数可消费的内存兼容形状，不作为持久化格式。"""
    result = screening_result_view(
        row,
        resume_profile=resume_profile,
        job_profile=job_profile,
        hard_screening=hard_screening,
    )
    summary = decision_summary(row)
    recommendation = dict(summary.get("recommendation") or {})
    return {
        # 以下字段保持旧卡片 DTO 的稳定形状；真实主键仍是 assessmentVersionId。
        "screeningAssessmentId": row.assessment_version_id,
        "applicationId": row.application_id,
        "sourceBundleRef": str((row.source_json or {}).get("sourceInputHash") or ""),
        "scoreStatus": "scored",
        "summary": str(
            dict(summary.get("ai_summary") or {}).get("recommendation_reason")
            or recommendation.get("reason")
            or recommendation.get("level")
            or ""
        ),
        "versionMetadata": {
            "bundleVersion": str((row.source_json or {}).get("schemaVersion") or "assessment_version_v1"),
            "generatedAt": (row.published_at or row.created_at).isoformat(),
            "source": "application_assessment_version",
        },
        "currentStage": row.stage,
        "screeningResultView": result,
        "decisionSummary": summary,
        "assessmentVersionId": row.assessment_version_id,
        "version": row.version,
    }


def decision_overview(
    row: ApplicationAssessmentVersion,
    *,
    resume_profile: dict[str, Any] | None = None,
    job_profile: dict[str, Any] | None = None,
    hard_screening: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = screening_result_view(
        row,
        resume_profile=resume_profile,
        job_profile=job_profile,
        hard_screening=hard_screening,
    )
    summary = decision_summary(row)
    return build_candidate_decision_overview(
        screening_result=result,
        decision_summary=summary,
        decision_support=build_decision_support(
            {"screeningResultView": result},
            decision_summary=summary,
        ),
    )




