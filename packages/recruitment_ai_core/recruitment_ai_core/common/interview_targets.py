from __future__ import annotations

import hashlib
from typing import Any


INTERVIEW_TARGET_LIMIT = 5
_TRIGGER_PRIORITY = {
    "missing_core_evidence": 0,
    "weak_core_result_l1": 1,
    "weak_core_result_l2": 2,
    "high_result_narrow_proof": 3,
}


def build_interview_targets(
    *,
    job_profile: dict[str, Any],
    capability_results: list[dict[str, Any]],
    pair_assessments: list[dict[str, Any]] | None = None,
    project_indicator_results: list[dict[str, Any]] | None = None,
    application_id: str = "",
    stage: str,
    risks: list[dict[str, Any]] | None = None,
    limit: int = INTERVIEW_TARGET_LIMIT,
) -> list[dict[str, Any]]:
    # 1. 初筛面试目标只由岗位能力结果和经历指标结果生成，不再把历史风险对象混入。
    del risks
    # 2. 建立岗位定义、评分结果和证据配对索引，后续每个目标都能反查来源。
    pairs = list(pair_assessments or [])
    definitions = {
        str(item.get("job_capability_id")): item
        for item in job_profile.get("job_capabilities", [])
        if item.get("job_capability_id")
    }
    results = {
        str(item.get("job_capability_id")): item
        for item in capability_results
        if item.get("job_capability_id")
    }
    legacy_live_ids = {
        str(capability.get("job_capability_id"))
        for unit in job_profile.get("assessment_units", [])
        for capability in unit.get("capabilities", [])
        if capability.get("job_capability_id")
    }
    forced: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    live_quality_focus_ids: set[str] = set()

    # 3. 逐岗位能力判断是否需要面试补证或复核，核心能力优先于辅助能力。
    for capability_id, definition in definitions.items():
        result = results.get(capability_id, {})
        selected_pairs = _independent_pairs(result, pairs)
        level = max(
            (int(item.get("content_level") or 0) for item in selected_pairs),
            default=0,
        )
        role = str(definition.get("role") or "supporting").lower()
        assessment_mode = str(
            definition.get("assessment_mode")
            or ("live" if capability_id in legacy_live_ids else "experience")
        ).lower()
        source_result_ids = _strings(
            [result.get("job_capability_result_id")]
        )

        # 4. 配置为现场演示的能力强制生成目标，不受简历证据强弱影响。
        if assessment_mode == "live":
            live_quality_focus_ids.update(
                str(value)
                for value in definition.get("quality_focus_ids", [])
                if value
            )
            forced.append(_target(
                application_id=application_id,
                purpose="direct_demonstration",
                target_type="job_capability",
                target_id=capability_id,
                source_result_ids=source_result_ids,
                trigger_code="configured_live_assessment",
                verification_goal=(
                    f"现场展示{_name(definition)}，并按岗位配置的评分标准判断完成质量。"
                ),
                stage=stage,
                role=role,
                independent_proof_count=len(selected_pairs),
                forced=True,
                level=level,
            ))
            continue

        # 5. 核心能力没有有效简历证据时，要求面试补充具体经历。
        if role == "core" and level == 0:
            candidates.append(_target(
                application_id=application_id,
                purpose="elicit_missing",
                target_type="job_capability",
                target_id=capability_id,
                source_result_ids=source_result_ids,
                trigger_code="missing_core_evidence",
                verification_goal=f"补充能够证明{_name(definition)}的实际经历、个人动作和结果。",
                stage=stage,
                role=role,
                independent_proof_count=0,
                level=0,
            ))
        # 6. 核心能力证据偏弱时，要求核验参与深度、个人动作和结果。
        elif role == "core" and level in {1, 2}:
            candidates.append(_target(
                application_id=application_id,
                purpose="verify_experience",
                target_type="job_capability",
                target_id=capability_id,
                source_result_ids=source_result_ids,
                trigger_code="weak_core_result",
                verification_goal=f"核验{_name(definition)}的实际参与深度、个人动作和结果依据。",
                stage=stage,
                role=role,
                independent_proof_count=len(selected_pairs),
                level=level,
            ))
        # 7. 高分但只由单一独立证据支持时，要求核验其职责边界和可重复性。
        elif level >= 4 and len(selected_pairs) == 1:
            candidates.append(_target(
                application_id=application_id,
                purpose="verify_experience",
                target_type="job_capability",
                target_id=capability_id,
                source_result_ids=source_result_ids,
                trigger_code="high_result_narrow_proof",
                verification_goal=f"核验{_name(definition)}的职责边界、关键决策和可验证结果。",
                stage=stage,
                role=role,
                independent_proof_count=1,
                level=level,
            ))

    # 8. 识别核心岗位能力关注的预设经历指标，补足岗位能力配对之外的核验点。
    core_quality_focus_ids = {
        str(indicator_id)
        for capability in definitions.values()
        if str(capability.get("role") or "").lower() == "core"
        and str(capability.get("assessment_mode") or "experience").lower()
        == "experience"
        for indicator_id in capability.get("quality_focus_ids", [])
        if indicator_id
    }
    preset_by_indicator: dict[str, list[dict[str, Any]]] = {}
    for result in project_indicator_results or []:
        indicator_id = str(result.get("indicator_id") or "")
        if indicator_id:
            preset_by_indicator.setdefault(indicator_id, []).append(result)

    # 9. 对低等级或单项目高等级的核心经历指标生成额外核验目标。
    for indicator_id in sorted(core_quality_focus_ids - live_quality_focus_ids):
        rows = sorted(
            preset_by_indicator.get(indicator_id, []),
            key=lambda item: (
                -int(item.get("level") or 0),
                -float(item.get("score") or 0),
            ),
        )
        if not rows:
            continue
        level = max(int(item.get("level") or 0) for item in rows)
        independent_count = len({
            str(item.get("project_id") or item.get("project_indicator_result_id"))
            for item in rows
        })
        if level == 1:
            trigger = "weak_core_result"
        elif level >= 4 and independent_count == 1:
            trigger = "high_result_narrow_proof"
        else:
            continue
        source_ids = [
            str(item["project_indicator_result_id"])
            for item in rows
            if item.get("project_indicator_result_id")
        ]
        candidates.append(_target(
            application_id=application_id,
            purpose="verify_experience",
            target_type="preset_indicator",
            target_id=indicator_id,
            source_result_ids=source_ids,
            trigger_code=trigger,
            verification_goal=f"核验“{indicator_id}”在核心岗位经历中的实际表现和事实依据。",
            stage=stage,
            role="core_preset",
            independent_proof_count=independent_count,
            level=level,
        ))

    # 10. 去重后先保留强制现场目标，再按核心性、触发原因和证据独立性选取剩余目标。
    forced = _deduplicate(forced)
    remaining = max(0, limit - len(forced))
    ranked = sorted(_deduplicate(candidates), key=_sort_key)
    return [_public(item) for item in [*forced, *ranked[:remaining]]]


def merge_interview_targets(
    existing: list[dict[str, Any]],
    generated: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_id = {
        str(item.get("interview_target_id")): dict(item)
        for item in existing
        if item.get("interview_target_id")
    }
    output: list[dict[str, Any]] = []
    for item in generated:
        target_id = str(item["interview_target_id"])
        previous = by_id.get(target_id)
        if previous and previous.get("status") == "resolved":
            output.append(previous)
        else:
            output.append({**(previous or {}), **item})
    return output


def _target(
    *,
    application_id: str,
    purpose: str,
    target_type: str,
    target_id: str,
    source_result_ids: list[str],
    trigger_code: str,
    verification_goal: str,
    stage: str,
    role: str,
    independent_proof_count: int,
    level: int,
    forced: bool = False,
) -> dict[str, Any]:
    identity = _stable_id(purpose, target_type, target_id)
    return {
        "interview_target_id": identity,
        "application_id": application_id,
        "purpose": purpose,
        "target_type": target_type,
        "target_id": target_id,
        "source_result_ids": list(dict.fromkeys(source_result_ids)),
        "trigger_code": trigger_code,
        "verification_goal": verification_goal,
        "status": "open",
        "stage_created": stage,
        "_role": role,
        "_independent_proof_count": independent_proof_count,
        "_level": level,
        "_forced": forced,
    }


def _independent_pairs(
    result: dict[str, Any],
    pair_assessments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_id = {
        str(item.get("pair_id")): item
        for item in pair_assessments
        if item.get("pair_id")
    }
    rows = [
        by_id[pair_id]
        for pair_id in [
            result.get("primary_pair_id"),
            *list(result.get("supplemental_pair_ids") or []),
        ]
        if pair_id in by_id and int(by_id[pair_id].get("content_level") or 0) > 0
    ]
    rows.sort(
        key=lambda item: (
            -int(item.get("content_level") or 0),
            -float(item.get("pair_score") or 0),
        )
    )
    selected: list[dict[str, Any]] = []
    used: set[str] = set()
    for item in rows:
        footprint = {
            f"WU:{value}"
            for value in item.get("proof_work_unit_ids", [])
            if value
        } or {
            f"{item.get('evidence_type')}:{item.get('evidence_id') or item.get('pair_id')}"
        }
        if footprint & used:
            continue
        selected.append(item)
        used.update(footprint)
    return selected


def _sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
    role_rank = {
        "core": 0,
        "core_preset": 1,
        "supporting": 2,
    }.get(str(item.get("_role")), 9)
    trigger = str(item.get("trigger_code") or "")
    trigger_key = (
        f"weak_core_result_l{item.get('_level')}"
        if trigger == "weak_core_result"
        else trigger
    )
    return (
        role_rank,
        _TRIGGER_PRIORITY.get(trigger_key, 9),
        int(item.get("_independent_proof_count") or 0),
        str(item.get("target_id") or ""),
    )


def _deduplicate(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in items:
        key = (
            str(item.get("purpose")),
            str(item.get("target_type")),
            str(item.get("target_id")),
        )
        current = by_key.get(key)
        if current is None:
            by_key[key] = item
            continue
        current["source_result_ids"] = list(dict.fromkeys([
            *current.get("source_result_ids", []),
            *item.get("source_result_ids", []),
        ]))
    return list(by_key.values())


def _public(item: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in item.items()
        if not key.startswith("_")
    }


def _name(definition: dict[str, Any]) -> str:
    return str(
        definition.get("capability_name")
        or definition.get("capability_definition")
        or "该岗位能力"
    )


def _strings(values: list[Any]) -> list[str]:
    return [str(value) for value in values if value]


def _stable_id(*parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"IT_{digest}"
