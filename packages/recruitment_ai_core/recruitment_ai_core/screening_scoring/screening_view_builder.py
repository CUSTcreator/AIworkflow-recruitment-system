from __future__ import annotations

from typing import Any

from .resume_experience.preset_models import get_preset_model, model_indicators_by_id


CONTENT_DESCRIPTIONS = {
    0: "当前证据与该岗位能力没有有效关联。",
    1: "仅体现邻近背景、简单接触或技能声明。",
    2: "可以迁移部分相关经验，但尚未覆盖核心职责。",
    3: "已支持核心行为，但与岗位场景仍有部分差距。",
    4: "较完整覆盖核心行为，相关经验可以较快迁移。",
    5: "能力边界与岗位要求高度一致，经验基本可以直接复用。",
}


def build_screening_result_view(
    *,
    application_id: str,
    candidate_profile: dict[str, Any],
    job_profile: dict[str, Any],
    score_output: dict[str, Any],
    qualification: dict[str, Any],
    resume_profile: dict[str, Any],
    interview_targets: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    # 1. 将算法结果投影为初筛工作台 DTO；这里不计算新分数，也不修改画像。
    return {
        "applicationId": application_id,
        "summary": {
            "overallScore": score_output.get("base_score"),
            "jobCapabilityFitScore": score_output.get("job_capability_fit_score"),
            "resumeExperienceScore": score_output.get(
                "resume_experience_score",
                score_output.get("resume_demonstrated_capability_score"),
            ),
            "educationBackgroundScore": score_output.get(
                "education_background_score"
            ),
            "qualificationStatus": qualification.get("gate", "unclear"),
            "scoreExplanation": score_output.get("score_explanation_detail"),
        },
        # 2. 按岗位单元展示每项能力、匹配等级和可点击的证据引用。
        "jobRequirements": _job_requirement_views(candidate_profile, job_profile),
        # 3. 展示候选人的预设经历能力、优势和薄弱项。
        "resumeCapabilities": _resume_capability_views(candidate_profile),
        # 4. 将已生成的面试核验目标转换为初筛页面的面试重点。
        "interviewFocus": {
            "firstInterviewFocus": _interview_focus(interview_targets or []),
        },
        # 5. 建立证据索引，使页面任意评分结论都能回跳到简历原文。
        "evidenceIndex": _evidence_index(candidate_profile, resume_profile),
        "viewSchemaVersion": "screening_result_view_v2_0",
    }


def _job_requirement_views(
    profile: dict[str, Any], job_profile: dict[str, Any]
) -> list[dict[str, Any]]:
    job_result = profile.get("job_result") or {}
    pair_assessments = {
        item.get("pair_id"): item
        for item in job_result.get("pair_assessments", [])
    }
    capability_results = {
        item.get("job_capability_id"): item
        for item in job_result.get("capability_results", [])
    }
    unit_results = {
        item.get("job_unit_id"): item
        for item in job_result.get("job_unit_results", [])
    }
    output: list[dict[str, Any]] = []
    units = [
        *job_profile.get("jd_units", []),
        *job_profile.get("assessment_units", []),
    ]
    for unit in units:
        unit_id = unit.get("job_unit_id") or unit.get("assessment_unit_id")
        capabilities = []
        for capability in unit.get("capabilities", []):
            result = capability_results.get(
                capability.get("job_capability_id"), {}
            )
            evidence = [
                pair_assessments[pair_id]
                for pair_id in [
                    result.get("primary_pair_id"),
                    *result.get("supplemental_pair_ids", []),
                ]
                if pair_id in pair_assessments
            ]
            capabilities.append(
                {
                    "capabilityId": capability.get("job_capability_id"),
                    "name": capability.get("capability_name"),
                    "definition": capability.get("capability_definition"),
                    "role": capability.get("role", "supporting"),
                    "score": _percent(result.get("score")),
                    "contentFitDescription": CONTENT_DESCRIPTIONS.get(
                        max(
                            (
                                int(item.get("content_level") or 0)
                                for item in evidence
                            ),
                            default=0,
                        ),
                        CONTENT_DESCRIPTIONS[0],
                    ),
                    "evidenceQualityDescription": _quality_description(evidence),
                    "evidence": [
                        _job_evidence_view(item) for item in evidence[:3]
                    ],
                }
            )
        if not capabilities:
            continue
        aggregate = unit_results.get(unit_id, {})
        output.append(
            {
                "jobUnitId": unit_id,
                "sourceText": unit.get("raw_text")
                or unit.get("title")
                or str(unit_id),
                "sourceSection": unit.get("section", "responsibility"),
                "score": _percent(aggregate.get("score")),
                "coreCapabilities": [
                    item for item in capabilities if item["role"] == "core"
                ],
                "supportingCapabilities": [
                    item for item in capabilities if item["role"] != "core"
                ],
            }
        )
    return output


def _job_evidence_view(item: dict[str, Any]) -> dict[str, Any]:
    level = int(item.get("content_level") or 0)
    source_ids = item.get("proof_work_unit_ids") or [item.get("evidence_id")]
    return {
        "evidenceId": item.get("evidence_id"),
        "sourceEvidenceIds": [
            evidence_id for evidence_id in source_ids if evidence_id
        ],
        "evidenceType": item.get("evidence_type"),
        "projectId": None,
        "score": _percent(item.get("pair_score")),
        "contentFitDescription": CONTENT_DESCRIPTIONS.get(
            level, CONTENT_DESCRIPTIONS[0]
        ),
        "qualityScore": _percent(item.get("quality_score")),
        "reason": item.get("reason", ""),
    }


def _quality_description(evidence: list[dict[str, Any]]) -> str:
    if not evidence:
        return "未找到可用于判断的简历证据。"
    quality = max(float(item.get("quality_score") or 0) for item in evidence)
    if quality >= 0.75:
        return "证据不仅相关，而且体现了较强的方案、工程或结果质量。"
    if quality >= 0.45:
        return "证据质量中等，能够说明做过，但深度或结果完整性仍有限。"
    if quality > 0:
        return "证据存在，但对实际完成质量的证明较弱。"
    return "本项主要依据内容相关性判断，未获得额外的经历质量加成。"


def _resume_capability_views(profile: dict[str, Any]) -> list[dict[str, Any]]:
    preset_result = profile.get("preset_experience_result") or {}
    preset_model = get_preset_model(
        profile.get("preset_model_id"),
        profile.get("preset_model_version"),
    )
    specs = model_indicators_by_id(preset_model)
    explanatory_by_framework = _project_indicator_views_by_framework(
        preset_result.get("project_indicator_results", []),
        specs,
    )
    output = []
    for framework in preset_result.get("candidate_framework_results", []):
        indicators = []
        framework_id = str(framework.get("framework_id") or "")
        source_indicators = explanatory_by_framework.get(framework_id, [])
        for item in source_indicators:
            indicator_id = item.get("indicator_id")
            level = int(item.get("level") or 0)
            spec = specs.get(indicator_id, {})
            rubric = spec.get("project_rubric") or spec.get("levels") or []
            description = (
                rubric[level - 1]
                if level > 0 and level <= len(rubric)
                else "现有经历不足以形成有效判断。"
            )
            project_id = item.get("primary_project_id")
            indicators.append(
                {
                    "indicatorId": indicator_id,
                    "name": spec.get("name") or indicator_id,
                    "score": _percent(item.get("score")),
                    "levelDescription": description,
                    "primaryProjectId": project_id,
                    "primaryProjectName": project_id,
                    "supportProjectIds": item.get("support_project_ids", []),
                    "evidenceIds": item.get("supporting_evidence_ids", []),
                }
            )
        output.append(
            {
                "frameworkId": framework.get("framework_id"),
                "frameworkName": next(
                    (
                        item.get("name")
                        for item in preset_model.get("frameworks", [])
                        if item.get("framework_id") == framework_id
                    ),
                    framework_id,
                ),
                "score": _percent(framework.get("score")),
                "activatedIndicatorCount": len(indicators),
                "indicatorCount": sum(
                    spec.get("framework_id") == framework_id
                    for spec in specs.values()
                ),
                "indicators": indicators,
            }
        )
    return output


def _project_indicator_views_by_framework(
    project_results: list[dict[str, Any]],
    specs: dict[str, dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Derive page rows from formal project results."""
    output: dict[str, list[dict[str, Any]]] = {}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in project_results:
        indicator_id = str(item.get("indicator_id") or "")
        if indicator_id:
            grouped.setdefault(indicator_id, []).append(item)
    for indicator_id, rows in grouped.items():
        framework_id = str(specs.get(indicator_id, {}).get("framework_id") or "")
        ordered = sorted(
            rows,
            key=lambda item: float(item.get("score") or 0),
            reverse=True,
        )[:3]
        if not framework_id or not ordered:
            continue
        primary = ordered[0]
        evaluation = primary.get("project_evaluation") or {}
        output.setdefault(framework_id, []).append({
            "indicator_id": indicator_id,
            "score": float(primary.get("score") or 0),
            "level": int(evaluation.get("level") or 0),
            "primary_project_id": primary.get("project_id"),
            "support_project_ids": [
                item.get("project_id") for item in ordered[1:]
                if item.get("project_id")
            ],
            "supporting_evidence_ids": list(
                dict.fromkeys(
                    str(value)
                    for value in evaluation.get("evidence_work_unit_ids", [])
                    if value
                )
            ),
        })
    for rows in output.values():
        rows.sort(key=lambda item: float(item["score"]), reverse=True)
    return output


def _interview_focus(targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rank = {"high": 0, "medium": 1, "low": 2}
    return [
        {
            "focusId": target.get("interview_target_id"),
            "title": target.get("remaining_need") or "",
            "expectedEvidence": target.get("expected_result_type") or "",
            "priority": target.get("priority", "medium"),
            "evidenceIds": target.get("source_evidence_ids", []),
        }
        for target in sorted(
            targets, key=lambda item: rank.get(str(item.get("priority")), 1)
        )[:4]
        if target.get("remaining_need")
    ]


def _evidence_index(
    profile: dict[str, Any], resume_profile: dict[str, Any]
) -> dict[str, Any]:
    ids: set[str] = set()
    preset_result = profile.get("preset_experience_result") or {}
    job_result = profile.get("job_result") or {}
    for item in preset_result.get("work_unit_indicator_results", []):
        if item.get("work_unit_id"):
            ids.add(str(item["work_unit_id"]))
    for item in job_result.get("pair_assessments", []):
        ids.update(str(value) for value in item.get("proof_work_unit_ids", []))
        if item.get("evidence_type") in {"work_unit", "skill_claim"}:
            if item.get("evidence_id"):
                ids.add(str(item["evidence_id"]))
    from .profile_evidence import nested_source_bullets, nested_work_units

    work_units = {
        item.get("work_unit_id"): item
        for item in nested_work_units(resume_profile)
    }
    source_bullets = {
        item.get("source_bullet_id"): item
        for item in nested_source_bullets(resume_profile)
    }
    result = {}
    for evidence_id in sorted(ids):
        source = work_units.get(evidence_id) or source_bullets.get(evidence_id) or {}
        result[evidence_id] = {
            "evidenceId": evidence_id,
            "rawText": source.get("raw_text", ""),
            "sourceLineStart": source.get("source_line_start"),
            "sourceLineEnd": source.get("source_line_end"),
            "sourceBulletId": source.get("source_bullet_id"),
        }
    return result


def build_evidence_index(
    profile: dict[str, Any], resume_profile: dict[str, Any]
) -> dict[str, Any]:
    """公开提供核心结果的证据索引构建，不生成页面 DTO。"""
    return _evidence_index(profile, resume_profile)

def _percent(value: Any) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    return round(float(value) * 100, 2)


def _number(value: Any) -> float | None:
    return round(float(value), 2) if isinstance(value, (int, float)) else None
