"""V1/V2/V3 共用的确定性评估规则。

该模块只接收普通 JSON 结构并返回普通 JSON 结构：不访问数据库、不调用 LLM、
不生成页面文案。后端 Workflow 负责冻结输入、持久化与状态流转；任何阶段都不得
在后端另写一套强弱项、推荐或 Target 的业务判断。
"""
from __future__ import annotations

from typing import Any, Mapping

from recruitment_ai_core.common.interview_targets import build_interview_targets
from recruitment_ai_core.screening_scoring.strength_signals import build_strength_signals
from recruitment_ai_core.screening_scoring.weakness_signals import build_weakness_signals


def derive_assessment_rules(
    *,
    stage: str,
    core_result: Mapping[str, Any],
    job_profile: Mapping[str, Any],
    application_id: str,
    previous_rule_result: Mapping[str, Any] | None = None,
    open_targets: list[Mapping[str, Any]] | None = None,
    resolved_interview_target_ids: set[str] | None = None,
) -> dict[str, Any]:
    """从统一核心评分结果派生 V1/V2/V3 共用的规则结论。

    1. Strength/Weakness 的候选条件、排序与上限只复用算法包既有规则；
    2. Signal key 同时编码 ``strength|weakness``、来源类型和目标 ID，保证跨版本稳定；
    3. Target 由当前完整能力结果重新候选，再与旧 Target 合并为 open/resolved，
       不能因为展示层或页面排序而改变其状态。
    """
    core = dict(core_result or {})
    experience = _mapping(core.get("experience_result") or core.get("preset_experience_result"))
    job = _mapping(core.get("job_result"))
    capabilities = _items(job.get("capability_results"))
    pairs = _items(job.get("pair_assessments"))
    project_indicators = _items(experience.get("project_indicator_results"))
    work_unit_indicators = _items(experience.get("work_unit_indicator_results"))
    definitions = _items(job_profile.get("job_capabilities"))

    strengths = _signals(
        build_strength_signals(
            project_indicator_results=project_indicators,
            work_unit_indicator_results=work_unit_indicators,
            job_capability_results=capabilities,
            pair_assessments=pairs,
            job_capabilities=definitions,
        ),
        kind="strength",
    )
    weaknesses = _signals(
        build_weakness_signals(
            project_indicator_results=project_indicators,
            job_capability_results=capabilities,
            pair_assessments=pairs,
            job_capabilities=definitions,
        ),
        kind="weakness",
    )
    prior = dict(previous_rule_result or {})
    # InterviewTarget 是跨阶段的申请级业务对象：只有初筛 V1 根据完整结果首次创建。
    # V2/V3 不再重新候选，避免“本轮排名变化”被误解释为“原核验事项已经完成”。
    target_candidates = (
        build_interview_targets(
            application_id=application_id,
            job_profile=dict(job_profile),
            capability_results=capabilities,
            pair_assessments=pairs,
            project_indicator_results=project_indicators,
            stage=stage,
        )
        if stage == "screening"
        else []
    )
    target_updates = _target_updates(
        target_candidates,
        open_targets or [],
        resolved_interview_target_ids or set(),
        job_profile,
        stage,
    )
    return {
        "schema_version": "assessment_rule_result_v1",
        "strength_signals": strengths,
        "weakness_signals": weaknesses,
        "target_updates": target_updates,
        "signal_comparison": {
            "baseline_assessment_version_id": str(
                prior.get("assessment_version_id") or prior.get("assessmentVersionId") or ""
            ),
            "strengths": _compare_signals(_items(prior.get("strength_signals")), strengths),
            "weaknesses": _compare_signals(_items(prior.get("weakness_signals")), weaknesses),
        },
    }


def _signals(rows: list[dict[str, Any]], *, kind: str) -> list[dict[str, Any]]:
    """保留规则事实，绝不在此处写最终标题或摘要。"""
    result: list[dict[str, Any]] = []
    for rank, row in enumerate(rows, start=1):
        source_type = str(row.get("source_type") or "")
        target_id = str(row.get("target_id") or "")
        if not source_type or not target_id:
            continue
        result.append({
            "signal_key": f"{kind}:{source_type}:{target_id}",
            "source_type": source_type,
            "target_id": target_id,
            "source_result_ids": _strings(row.get("source_result_ids")),
            "current_rank": rank,
            "previous_rank": None,
        })
    return result


def _compare_signals(previous: list[dict[str, Any]], current: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """对两个 Top 4 集合取并集；状态仅由稳定 key 决定。"""
    before = {str(item.get("signal_key") or ""): item for item in previous if item.get("signal_key")}
    after = {str(item.get("signal_key") or ""): item for item in current if item.get("signal_key")}
    output: list[dict[str, Any]] = []
    for key in [*after, *(item for item in before if item not in after)]:
        old, new = before.get(key), after.get(key)
        source = dict(new or old or {})
        source["previous_rank"] = old.get("current_rank") if old else None
        source["current_rank"] = new.get("current_rank") if new else None
        source["change_status"] = "retained" if old and new else ("added" if new else "closed")
        output.append(source)
    return output[:8]


def _target_updates(
    candidates: list[dict[str, Any]],
    existing: list[Mapping[str, Any]],
    resolved_interview_target_ids: set[str],
    job_profile: Mapping[str, Any],
    stage: str,
) -> list[dict[str, Any]]:
    """按 Application 维护同一组 InterviewTarget，不跨阶段复制。

    - V1：将规则候选首次物化为 Target；
    - V2/V3：遍历冻结时仍为 open 的 V1 Target，只决定继续 open 或 resolved。

    后续阶段即便当前评分发现新的候选项，也只能作为本轮 Signal/展示信息，不能凭空
    创建新的 InterviewTarget；否则面试目标会在每个版本膨胀，失去跨轮追踪意义。
    """
    candidate_by_id = {
        str(item.get("interview_target_id") or ""): dict(item)
        for item in candidates
    }
    existing_by_id = {
        str(item.get("interview_target_id") or ""): dict(item)
        for item in existing
    }
    if stage == "screening":
        output: list[dict[str, Any]] = []
        for candidate in candidate_by_id.values():
            target = dict(candidate)
            target["title"] = _target_name(target, job_profile)
            target["status"] = "open"
            target["resolved_stage"] = None
            target["resolution_note"] = None
            output.append(target)
        return output

    output: list[dict[str, Any]] = []
    for target_id, previous in existing_by_id.items():
        # 后续版本不可改写 Target 身份、标题、触发规则或创建阶段，只能改状态。
        # 关闭必须来自面评解析明确返回的 V1 interview_target_id；能力分数、候选排名和
        # Signal 的增减都只是本轮评估结果，不能证明既有核验事项已完成。
        target = dict(previous)
        resolved = target_id in resolved_interview_target_ids
        target["status"] = "resolved" if resolved else "open"
        target["resolved_stage"] = stage if resolved else None
        target["resolution_note"] = "本轮面试已获得该事项所需的明确核验证据" if resolved else None
        output.append(target)
    return output

def _target_name(target: Mapping[str, Any], job_profile: Mapping[str, Any]) -> str:
    target_id = str(target.get("target_id") or "")
    if str(target.get("target_type") or "") == "job_capability":
        for row in _items(job_profile.get("job_capabilities")):
            if str(row.get("job_capability_id") or "") == target_id:
                return str(row.get("capability_name") or row.get("capability_definition") or "岗位能力要求")
    return "经历能力表现" if target_id else "待核验能力"


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _items(value: Any) -> list[dict[str, Any]]:
    return [dict(item) for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []


def _strings(value: Any) -> list[str]:
    return list(dict.fromkeys(str(item) for item in value if item)) if isinstance(value, list) else []

