from __future__ import annotations

import math
from typing import Any

from recruitment_ai_core.execution import map_bounded
from recruitment_ai_core.common.scoring_evidence import is_usable_project_evidence

from .aggregation import (
    aggregate_candidate_frameworks,
    aggregate_project_frameworks,
    aggregate_project_indicators,
    aggregate_resume_score,
)
from .preset_models import (
    MODEL_POLICY_VERSION as POLICY_VERSION,
    MODEL_PROMPT_VERSION as PROMPT_VERSION,
    MODEL_RUBRIC_VERSION as RUBRIC_VERSION,
    get_preset_model,
    preset_catalog,
)
from .indicator_evaluator import evaluate_indicators
from .llm_gateway import ResumeExperienceLLMGateway
from .preprocessing import prepare_existing_work_units, prepare_project


ALGORITHM_VERSION = "preset_experience_assessment_v2_0"
FORMULA_VERSION = "preset_experience_formula_v2_1"

# 只在同一候选人至少有三段完整评估经历时检测低分尾部。断层上方至少
# 保留两个项目作为稳定参照，避免仅凭一个特别突出的项目误删其他正常经历。
# 分数在这里转换为百分制；阈值 25 表示相差 25 个百分制分点。
MIN_NORMAL_PROJECTS_FOR_OUTLIER_FILTER = 3
MIN_STABLE_HIGH_PROJECTS = 2
LOW_SCORE_OUTLIER_GAP_POINTS = 25.0
PROJECT_SCORE_FRAMEWORKS = (
    "problem_context",
    "solution_execution",
    "outcome_value",
)


def _project_score_points(project: dict[str, Any]) -> float | None:
    """Return a temporary 100-point mean for one fully assessed project."""
    if project.get("status") in {"skipped", "unassessed"}:
        return None
    if bool(project.get("degraded")):
        return None
    scores = {
        str(item.get("framework_id") or ""): item.get("score")
        for item in project.get("project_framework_results", [])
        if isinstance(item, dict)
    }
    values: list[float] = []
    for framework_id in PROJECT_SCORE_FRAMEWORKS:
        try:
            value = float(scores[framework_id])
        except (KeyError, TypeError, ValueError):
            return None
        if not math.isfinite(value):
            return None
        values.append(value)
    return sum(values) / len(values) * 100.0


def _filter_low_score_projects(
    entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Drop a separated low-score tail before V1 aggregation.

    Only fully assessed projects form the score distribution. Degraded,
    skipped, or unassessed projects bypass this filter and keep their existing
    tolerant-degradation behavior. When equal maximum gaps exist, the later
    split is chosen so fewer projects are removed.
    """
    scored: list[tuple[int, float]] = []
    for index, entry in enumerate(entries):
        score = _project_score_points(dict(entry.get("project") or {}))
        if score is not None:
            scored.append((index, score))
    if len(scored) < MIN_NORMAL_PROJECTS_FOR_OUTLIER_FILTER:
        return entries

    ordered = sorted(scored, key=lambda item: item[1], reverse=True)
    candidate_splits: list[tuple[float, int]] = []
    for split_index in range(MIN_STABLE_HIGH_PROJECTS, len(ordered)):
        gap = ordered[split_index - 1][1] - ordered[split_index][1]
        if gap >= LOW_SCORE_OUTLIER_GAP_POINTS:
            candidate_splits.append((gap, split_index))
    if not candidate_splits:
        return entries

    _, split_index = max(candidate_splits, key=lambda item: (item[0], item[1]))
    outlier_indexes = {
        original_index for original_index, _score in ordered[split_index:]
    }
    return [entry for index, entry in enumerate(entries) if index not in outlier_indexes]


def assess_resume_experience(
    resume_profile: dict[str, Any],
    llm_config: dict[str, Any] | None = None,
    preset_model: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """兼容入口：按项目评分后汇总为候选人经历能力结果。

    工作流运行时会直接调用下方两个可恢复函数，让每一段经历拥有独立检查点；
    保留本入口供纯算法包的独立调用和历史测试使用。
    """
    model = preset_model or get_preset_model()
    gateway = ResumeExperienceLLMGateway(llm_config)
    global_limit = max(1, int(gateway.settings.execution.get("global_llm_max_in_flight", 12)))
    max_parallel_projects = max(
        1, int(gateway.settings.resume_experience.get("max_parallel_projects", global_limit))
    )
    project_results = map_bounded(
        "resume_project_assessment",
        list(resume_profile.get("experience_units", [])),
        lambda project: assess_resume_project(project, llm_config=llm_config, preset_model=model),
        max_concurrency=max_parallel_projects,
    )
    return assemble_resume_experience(
        resume_profile, project_results, llm_config=llm_config, preset_model=model
    )


def assess_resume_project(
    project: dict[str, Any],
    *,
    llm_config: dict[str, Any] | None = None,
    preset_model: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """评分一段经历并返回 JSON 结果，是初筛活动级恢复的最小业务边界。"""
    model = preset_model or get_preset_model()
    result, indicators, work_units, degraded, errors, audit = _assess_one_project(
        project, llm_config, model
    )
    return {
        "project": result,
        "indicators": indicators,
        "work_units": work_units,
        "degraded": degraded,
        "errors": errors,
        "audit": audit,
    }


def build_unassessed_resume_project(
    project: dict[str, Any],
    *,
    reason_code: str,
    error_message: str = "",
) -> dict[str, Any]:
    """构造一段经历的保守正式结果，不把技术失败记为零能力。

    该函数只用于 Activity 已完成全部外部重试后的本地降级。它仍沿用
    ``assess_resume_project`` 的返回合同，因此后续聚合、审计和发布不需要
    引入第二套临时能力模型。
    """
    project_id = str(project.get("experience_unit_id") or project.get("project_id") or "")
    detail = f"{reason_code}:{error_message[:240]}" if error_message else reason_code
    return {
        "project": {
            "project_id": project_id,
            "project_name": project.get("title") or project_id,
            "status": "unassessed",
            "assessment_status": "unassessed_due_to_degradation",
            "work_units": [],
            "project_pao_result": {
                "project_id": project_id,
                "problem": None,
                "action": None,
                "outcome": None,
            },
            "project_evidence": {
                "project_id": project_id,
                "project_pao_result": None,
                "project_indicator_results": [],
                "work_units": [],
            },
            "work_unit_indicator_results": [],
            "project_indicator_results": [],
            "project_framework_results": [],
            "degraded": True,
            "validation_errors": [detail],
        },
        "indicators": [],
        "work_units": [],
        "degraded": True,
        "errors": [detail],
        "audit": {
            "llm_used": False,
            "degraded": True,
            "errors": [detail],
            "traces": [],
        },
    }


def assemble_resume_experience(
    resume_profile: dict[str, Any],
    project_results: list[dict[str, Any]],
    *,
    llm_config: dict[str, Any] | None = None,
    preset_model: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """汇总已完成的项目活动，不再发起 LLM 或网络请求。"""
    model = preset_model or get_preset_model()
    gateway = ResumeExperienceLLMGateway(llm_config)
    entries: list[dict[str, Any]] = []
    skipped_experience_units: list[dict[str, Any]] = []
    degraded = False
    validation_errors: list[str] = []
    project_audits: list[dict[str, Any]] = []
    for item in project_results:
        project = dict(item.get("project") or {})
        indicators = list(item.get("indicators") or [])
        work_units = list(item.get("work_units") or [])
        project_degraded = bool(item.get("degraded"))
        errors = list(item.get("errors") or [])
        audit = dict(item.get("audit") or {})
        entries.append({
            "project": project,
            "indicators": indicators,
            "work_units": work_units,
        })

        degraded = degraded or project_degraded
        validation_errors.extend(errors)
        project_audits.append(audit)

    # 只有所有项目评分完成后才能判断离群项目；过滤发生在候选人级聚合前，
    # 因此被删除项目不会进入经历总分，也不会进入后续岗位能力证据。
    kept_entries = _filter_low_score_projects(entries)
    projects = [dict(entry["project"]) for entry in kept_entries]
    project_indicators = [
        indicator
        for entry in kept_entries
        for indicator in entry["indicators"]
    ]
    for project in projects:
        if project.get("status") == "skipped":
            skipped_experience_units.append({
                "experience_unit_id": project["project_id"],
                "title": project["project_name"],
                "reason_code": project["skip_reason"],
                "message": "该段经历未包含可验证的工作事实，未纳入经历能力评分。",
            })
    project_frameworks = [
        result for project in projects
        for result in project.get("project_framework_results", [])
    ]
    frameworks = aggregate_candidate_frameworks(project_frameworks, model)
    resume_score = aggregate_resume_score(frameworks, project_indicators)

    audit = _merge_llm_audits(gateway.audit(degraded), project_audits, degraded)
    return {
        "algorithm_version": ALGORITHM_VERSION,
        "preset_model_id": model["model_id"],
        "preset_model_version": model["version"],
        "catalog": preset_catalog(model),
        "policy_version": POLICY_VERSION,
        "prompt_version": PROMPT_VERSION,
        "rubric_version": RUBRIC_VERSION,
        "llm_audit": audit,
        "degraded": degraded,
        "validation_errors": validation_errors,
        "skipped_experience_units": skipped_experience_units,
        "project_experience_assessments": projects,
        "work_unit_indicator_results": [
            result for project in projects
            for result in project.get("work_unit_indicator_results", [])
        ],
        "project_pao_results": [project["project_pao_result"] for project in projects],
        "project_indicator_results": project_indicators,
        "project_framework_results": project_frameworks,
        "candidate_framework_results": frameworks,
        "risks": [],
        "score_summary": {
            "resume_experience_score": resume_score,
            "preset_experience_score": resume_score,
            "active_framework_count": sum(item["score"] > 0 for item in frameworks),
            "active_indicator_count": len({item["indicator_id"] for item in project_indicators}),
            "formula_version": FORMULA_VERSION,
        },
    }

def _assess_one_project(
    project: dict[str, Any],
    llm_config: dict[str, Any] | None,
    preset_model: dict[str, Any],
) -> tuple[
    dict[str, Any],
    list[dict[str, Any]],
    list[dict[str, Any]],
    bool,
    list[str],
    dict[str, Any],
]:
    # 1. 为单个项目建立独立调用网关，避免并行项目之间共享可变状态。
    gateway = ResumeExperienceLLMGateway(llm_config)
    project_id = str(project.get("experience_unit_id") or project.get("project_id"))
    # 2. 校验并标准化该项目的工作单元；每个单元必须保留可引用的简历原文。
    work_units, prep_degraded, prep_errors = prepare_project(project, gateway)
    # 3. 空经历不允许驱动模型猜测；以“跳过”产物返回，让其余有效项目继续评分。
    if not work_units:
        return _skipped_project_assessment(project, prep_errors, gateway)
    # 4. 对工作单元逐项判定指标，再生成项目层的问题、方法、结果和跨单元指标结论。
    unit_results, project_pao, project_level_results, eval_degraded, eval_errors = evaluate_indicators(
        work_units, gateway, project_id, list(project.get("context_items") or []),
        preset_model=preset_model,
    )
    # 4. 合并同一项目中同指标的证据，防止同一句原文被重复放大。
    indicators = aggregate_project_indicators(
        project_id, unit_results, project_level_results, preset_model
    )
    # 5. 将项目指标映射为预设能力框架，形成可跨项目汇总的项目级结果。
    project_frameworks = aggregate_project_frameworks(project_id, indicators, preset_model)
    # 6. 汇总该项目的降级标记和校验错误，但保留已验证的确定性结果。
    degraded = prep_degraded or eval_degraded
    errors = [*prep_errors, *eval_errors]
    # 7. 返回项目结果、指标、工作单元、状态和审计轨迹，供上层统一聚合。
    return (
        {
            "project_id": project_id,
            "project_name": project.get("title") or project_id,
            "work_units": work_units,
        "project_pao_result": project_pao,
        "project_evidence": {
                "project_id": project_id,
                "project_pao_result": project_pao,
                "project_indicator_results": indicators,
                "work_units": work_units,
            },
            "work_unit_indicator_results": unit_results,
            "project_indicator_results": indicators,
            "project_framework_results": project_frameworks,
            "degraded": degraded,
            "validation_errors": errors,
        },
        indicators,
        work_units,
        degraded,
        errors,
        gateway.audit(degraded),
    )


def _skipped_project_assessment(
    project: dict[str, Any],
    errors: list[str],
    gateway: ResumeExperienceLLMGateway,
) -> tuple[
    dict[str, Any],
    list[dict[str, Any]],
    list[dict[str, Any]],
    bool,
    list[str],
    dict[str, Any],
]:
    """将无可验证工作事实的经历作为局部降级结果发布，而不是终止整份初筛。"""
    project_id = str(project.get("experience_unit_id") or project.get("project_id"))
    reason = (
        "invalid_work_units"
        if any("invalid_work_units" in item for item in errors)
        else "no_scorable_work_units"
    )
    project_result = {
        "project_id": project_id,
        "project_name": project.get("title") or project_id,
        "status": "skipped",
        "skip_reason": reason,
        "work_units": [],
        "project_pao_result": {
            "project_id": project_id,
            "problem": None,
            "action": None,
            "outcome": None,
        },
        "project_evidence": {
            "project_id": project_id,
            "project_pao_result": None,
            "project_indicator_results": [],
            "work_units": [],
        },
        "work_unit_indicator_results": [],
        "project_indicator_results": [],
        "project_framework_results": [],
        "degraded": True,
        "validation_errors": errors,
    }
    return project_result, [], [], True, errors, gateway.audit(True)
def reassess_resume_experience(
    scoring_evidence: dict[str, Any],
    llm_config: dict[str, Any] | None = None,
    preset_model: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    model = preset_model or get_preset_model(
        scoring_evidence.get("preset_model_id"),
        scoring_evidence.get("preset_model_version"),
    )
    affected = set(scoring_evidence.get("affected_project_ids", []))
    if not affected:
        return None
    gateway = ResumeExperienceLLMGateway(llm_config)
    global_limit = max(
        1, int(gateway.settings.execution.get("global_llm_max_in_flight", 12))
    )
    max_parallel_projects = max(
        1,
        int(
            gateway.settings.resume_experience.get(
                "max_parallel_projects", global_limit
            )
        ),
    )
    previous = {
        item["project_id"]: item
        for item in scoring_evidence.get("project_assessments", [])
        if item.get("project_id") not in affected
    }
    project_names = {
        item["project_id"]: item.get("name") or item["project_id"]
        for item in scoring_evidence.get("projects", [])
    }
    project_contexts = {
        item["project_id"]: list(item.get("context_items") or [])
        for item in scoring_evidence.get("projects", [])
    }
    active_units: dict[str, list[dict[str, Any]]] = {}
    for unit in scoring_evidence.get("work_units", []):
        if unit.get("is_current", True):
            active_units.setdefault(unit["project_id"], []).append(unit)
    degraded, validation_errors = False, []
    project_audits: list[dict[str, Any]] = []
    inputs = [
        (
            project_id,
            active_units.get(project_id, []),
            project_names.get(project_id, project_id),
            project_contexts.get(project_id, []),
        )
        for project_id in sorted(affected)
        if active_units.get(project_id)
    ]
    project_results = map_bounded(
        "resume_project_reassessment",
        inputs,
        lambda item: _reassess_one_project(
            item[0], item[1], item[2], item[3], llm_config, model
        ),
        max_concurrency=max_parallel_projects,
    )
    for project, project_degraded, project_errors, project_audit in project_results:
        previous[project["project_id"]] = project
        degraded = degraded or project_degraded
        validation_errors.extend(project_errors)
        project_audits.append(project_audit)
    projects = [
        entry["project"]
        for entry in _filter_low_score_projects(
            [{"project": project} for project in previous.values()]
        )
    ]
    skipped_experience_units = [
        {
            "experience_unit_id": str(project.get("project_id") or ""),
            "title": str(project.get("project_name") or project.get("project_id") or ""),
            "reason_code": str(project.get("skip_reason") or "no_scorable_work_units"),
            "message": "该段经历未包含可验证的工作事实，未纳入经历能力评分。",
        }
        for project in projects
        if project.get("status") == "skipped"
    ]
    project_indicators = [
        result for project in projects
        for result in project.get("project_indicator_results", [])
    ]
    project_frameworks = [
        result for project in projects
        for result in project.get("project_framework_results", [])
    ]
    frameworks = aggregate_candidate_frameworks(project_frameworks, model)
    scoring_evidence["project_assessments"] = projects
    kept_project_ids = {
        str(project.get("project_id") or "") for project in projects
    }
    scoring_evidence["projects"] = [
        item
        for item in scoring_evidence.get("projects", [])
        if str(item.get("project_id") or "") in kept_project_ids
    ]
    # Reassessment starts from an existing evidence snapshot.  Remove stale
    # WorkUnits belonging to a newly filtered project before updating results.
    scoring_evidence["work_units"] = [
        item
        for item in scoring_evidence.get("work_units", [])
        if str(item.get("project_id") or "") in kept_project_ids
    ]
    scoring_evidence["project_evidence"] = [
        project["project_evidence"]
        for project in projects
        if is_usable_project_evidence(project.get("project_evidence"))
    ]
    indicators_by_unit: dict[str, list[dict[str, Any]]] = {}
    for project in projects:
        for result in project.get("work_unit_indicator_results", []):
            indicators_by_unit.setdefault(
                result["work_unit_id"], []
            ).append(result)
    target_ids_by_unit = {
        unit["work_unit_id"]: list(unit.get("target_ids", []))
        for project in projects
        for unit in project.get("work_units", [])
    }
    for version in scoring_evidence.get("work_units", []):
        if version.get("is_current", True) and version.get("work_unit_id") in target_ids_by_unit:
            version["target_ids"] = target_ids_by_unit[version["work_unit_id"]]
            version["work_unit_indicator_results"] = (
                indicators_by_unit.get(version["work_unit_id"], [])
            )
    return {
        "algorithm_version": ALGORITHM_VERSION,
        "preset_model_id": model["model_id"],
        "preset_model_version": model["version"],
        "catalog": preset_catalog(model),
        "policy_version": POLICY_VERSION,
        "prompt_version": PROMPT_VERSION,
        "rubric_version": RUBRIC_VERSION,
        "llm_audit": _merge_llm_audits(gateway.audit(degraded), project_audits, degraded),
        "degraded": degraded,
        "validation_errors": validation_errors,
        "skipped_experience_units": skipped_experience_units,
        "project_experience_assessments": projects,
        "work_unit_indicator_results": [
            result for project in projects
            for result in project.get("work_unit_indicator_results", [])
        ],
        "project_pao_results": [
            project["project_pao_result"] for project in projects
            if isinstance(project.get("project_pao_result"), dict)
        ],
        "project_indicator_results": project_indicators,
        "project_framework_results": project_frameworks,
        "candidate_framework_results": frameworks,
        "risks": [],
        "change_summary": {
            "change_type": "recalculated_affected_projects",
            "affected_project_ids": sorted(affected),
        },
        "score_summary": {
            "resume_experience_score": aggregate_resume_score(frameworks, project_indicators),
            "preset_experience_score": aggregate_resume_score(frameworks, project_indicators),
            "active_framework_count": sum(item["score"] > 0 for item in frameworks),
            "active_indicator_count": len({
                item["indicator_id"] for item in project_indicators
            }),
            "formula_version": FORMULA_VERSION,
        },
    }


def _reassess_one_project(
    project_id: str,
    units: list[dict[str, Any]],
    project_name: str,
    project_context: list[str],
    llm_config: dict[str, Any] | None,
    preset_model: dict[str, Any],
) -> tuple[dict[str, Any], bool, list[str], dict[str, Any]]:
    gateway = ResumeExperienceLLMGateway(llm_config)
    units, prep_degraded, prep_errors = prepare_existing_work_units(
        project_id, units, gateway, project_name=project_name
    )
    unit_results, project_pao, project_level_results, eval_degraded, eval_errors = evaluate_indicators(
        units, gateway, project_id, project_context, preset_model=preset_model
    )
    aggregated_indicators = aggregate_project_indicators(
        project_id, unit_results, project_level_results, preset_model
    )
    project_frameworks = aggregate_project_frameworks(project_id, aggregated_indicators, preset_model)
    degraded = prep_degraded or eval_degraded
    errors = [*prep_errors, *eval_errors]
    return (
        {
            "project_id": project_id,
            "project_name": project_name,
            "work_units": units,
        "project_pao_result": project_pao,
        "project_evidence": {
                "project_id": project_id,
                "project_pao_result": project_pao,
                "project_indicator_results": aggregated_indicators,
                "work_units": units,
            },
            "work_unit_indicator_results": unit_results,
            "project_indicator_results": aggregated_indicators,
            "project_framework_results": project_frameworks,
            "degraded": degraded,
            "validation_errors": errors,
        },
        degraded,
        errors,
        gateway.audit(degraded),
    )


def _merge_llm_audits(
    base: dict[str, Any],
    audits: list[dict[str, Any]],
    degraded: bool,
) -> dict[str, Any]:
    return {
        **base,
        "llm_used": any(item.get("llm_used") for item in audits),
        "degraded": degraded,
        "errors": [
            error for item in audits for error in item.get("errors", [])
        ],
        "traces": [
            trace for item in audits for trace in item.get("traces", [])
        ],
    }


def _project_summary(project: dict[str, Any]) -> dict[str, Any]:
    ranked = sorted(
        project["project_indicator_results"],
        key=lambda item: item["score"],
        reverse=True,
    )
    return {
        "project_id": project["project_id"],
        "project_name": project["project_name"],
        "strongest_indicator_ids": [item["indicator_id"] for item in ranked[:3]],
        "indicator_count": len(ranked),
        "work_unit_count": len(project["work_units"]),
    }
