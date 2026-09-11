"""V2/V3 冻结拓扑增量评分的纯函数实现。

算法包只处理普通字典和不可变 dataclass。它先验证冻结图的完整性与无环性，
再完成断言路由、非重叠锚点选择、AnchorUpdate 生成、增量应用和由叶到根的确定性聚合；
不访问 ORM、数据库或 LLM。LLM 的判断结果由上层 Activity 提供，本模块只依据冻结拓扑中的
rubric/level/观察规则转换分数。
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy

from recruitment_ai_core.common.interview_scoring import (
    LEVEL_SCORE, aggregate_target_round_results, apply_round_update,
)
from recruitment_ai_core.job_capability.current import _aggregate_job
from recruitment_ai_core.screening_scoring.resume_experience.aggregation import (
    aggregate_resume_score,
)
from recruitment_ai_core.screening_scoring.score_engine import score_candidate_profile
from typing import Any, Mapping

from .topology_contracts import (
    AnchorJudgement, AnchorSelection, AnchorUpdate, FrozenTopologyInput, InterviewAssertion,
    RegionRoute, ScoringRegion, TopologyScoringInput, TopologyScoringResult,
)


JUDGEMENT_OBSERVATION_SCORE = {
    AnchorJudgement.PARTIAL: LEVEL_SCORE[2],
    AnchorJudgement.SUPPORT: LEVEL_SCORE[3],
    AnchorJudgement.VERIFIED: LEVEL_SCORE[4],
    # 负向判断的观察分表达反证严重度，更新速度仍由公共公式中的负向系数控制。
    # not_support 不在映射中，因为无关或证据不足不能产生任何分数变化。
    AnchorJudgement.WEAK_CONTRADICTION: LEVEL_SCORE[2],
    AnchorJudgement.CONTRADICTED: LEVEL_SCORE[1],
    AnchorJudgement.STRONG_CONTRADICTION: LEVEL_SCORE[0],
}

# 单轮面试只能修正上一版评估，不能由一条模型判断一次性打穿已有证据。该保护
# 只在 V2/V3 拓扑流程显式启用，不改变公共公式其他调用方的既有行为。
POST_INTERVIEW_MAX_NEGATIVE_DELTA = 0.25


class TopologyScoringNotImplemented(RuntimeError):
    """历史异常名称，当前仅作为非法输入的明确合同错误类型保留。"""


def _nodes(frozen: FrozenTopologyInput) -> dict[str, dict[str, Any]]:
    """将冻结节点规范化为稳定 ID 索引；图结构永远以 Snapshot 为准。"""
    raw = frozen.topology.get("nodes") if isinstance(frozen.topology, Mapping) else ()
    result: dict[str, dict[str, Any]] = {}
    for row in raw or ():
        if not isinstance(row, Mapping):
            continue
        node_id = str(row.get("nodeId") or row.get("node_id") or "")
        if not node_id:
            continue
        if node_id in result:
            raise ValueError("topology_duplicate_node_id")
        result[node_id] = dict(row)
    return result


def _graph(frozen: FrozenTopologyInput, nodes: Mapping[str, Any]) -> tuple[dict[str, list[str]], dict[str, set[str]], tuple[str, ...]]:
    """验证冻结图并返回子边、全部祖先和叶到根的后序顺序。

    环不是正常业务状态，而是写入/迁移/人工修复造成的数据损坏。算法在进入评分前
    显式拒绝它，避免递归聚合无限循环或重复计算。
    """
    children: dict[str, list[str]] = defaultdict(list)
    parents: dict[str, set[str]] = defaultdict(set)
    seen_edges: set[tuple[str, str]] = set()
    for edge in frozen.topology.get("edges") or ():
        if not isinstance(edge, Mapping):
            continue
        parent = str(edge.get("parentNodeId") or edge.get("parent_node_id") or "")
        child = str(edge.get("childNodeId") or edge.get("child_node_id") or "")
        if parent not in nodes or child not in nodes:
            raise ValueError("topology_edge_node_missing")
        if parent == child:
            raise ValueError("topology_self_cycle_detected")
        if (parent, child) in seen_edges:
            raise ValueError("topology_duplicate_edge")
        seen_edges.add((parent, child))
        children[parent].append(child)
        parents[child].add(parent)

    colors: dict[str, int] = {}
    postorder: list[str] = []

    def visit(node_id: str) -> None:
        color = colors.get(node_id, 0)
        if color == 1:
            raise ValueError("topology_cycle_detected")
        if color == 2:
            return
        colors[node_id] = 1
        for child_id in sorted(children.get(node_id, ()), key=str):
            visit(child_id)
        colors[node_id] = 2
        postorder.append(node_id)

    for node_id in sorted(nodes):
        visit(node_id)

    ancestors: dict[str, set[str]] = {}
    for node_id in nodes:
        values: set[str] = set()
        frontier = list(parents.get(node_id, ()))
        while frontier:
            parent = frontier.pop()
            if parent in values:
                continue
            values.add(parent)
            frontier.extend(parents.get(parent, ()))
        ancestors[node_id] = values
    # postorder 保证子节点永远先于父节点，是递归聚合的执行顺序。
    return children, ancestors, tuple(postorder)


def _node_region(node: Mapping[str, Any]) -> ScoringRegion:
    value = str(node.get("region") or node.get("anchorType") or node.get("anchor_type") or "")
    if value in {"experience", "job", "both"}:
        return ScoringRegion(value)
    refs = node.get("references") or ()
    kinds = {str(item.get("kind") or item.get("reference_kind") or "") for item in refs if isinstance(item, Mapping)}
    if kinds & {"work_unit", "project", "preset_indicator", "experience", "candidate_framework"}:
        return ScoringRegion.EXPERIENCE
    if kinds & {"job_unit", "job_capability", "skill_claim", "jd_capability", "jd_unit"}:
        return ScoringRegion.JOB
    return ScoringRegion.BOTH


def route_assertions_to_regions(
    frozen: FrozenTopologyInput, assertions: tuple[InterviewAssertion, ...],
) -> tuple[RegionRoute, ...]:
    """内部阶段 1：将断言路由到其冻结图内的候选区域。"""
    nodes = _nodes(frozen)
    _graph(frozen, nodes)
    result: list[RegionRoute] = []
    for assertion in assertions:
        candidates = tuple(dict.fromkeys(str(item) for item in assertion.candidate_anchor_ids if str(item) in nodes))
        grouped: dict[ScoringRegion, list[str]] = defaultdict(list)
        for node_id in candidates:
            grouped[assertion.region or _node_region(nodes[node_id])].append(node_id)
        if not grouped:
            grouped[assertion.region or ScoringRegion.NON_SCORING] = []
        for region, ids in grouped.items():
            result.append(RegionRoute(assertion_id=assertion.assertion_id, region=region, candidate_anchor_ids=tuple(ids)))
    return tuple(result)


def select_non_overlapping_anchors(
    frozen: FrozenTopologyInput, routes: tuple[RegionRoute, ...],
) -> tuple[AnchorSelection, ...]:
    """内部阶段 2：同一断言、同一区域只保留不处于同一路径的锚点。

    选择不是只看直接父节点：若父、祖父或更高层节点已被命中，所有后代均被遮蔽。
    因而一段面评不会沿同一能力路径被重复计分；不同分支仍可同时保留。
    """
    nodes = _nodes(frozen)
    _, ancestors, _ = _graph(frozen, nodes)
    output: list[AnchorSelection] = []
    for route in routes:
        candidates = list(dict.fromkeys(item for item in route.candidate_anchor_ids if item in nodes))
        # 同一面评单元、同一分支必须优先保留更高层锚点；祖先越少，层级越高。
        # ordinal 仅用于同层的稳定排序，不能反过来让更深的节点抢占父节点。
        candidates.sort(key=lambda item: (len(ancestors[item]), int(nodes[item].get("ordinal") or 0), item))
        selected: list[str] = []
        selected_set: set[str] = set()
        masked: list[str] = []
        for anchor_id in candidates:
            if ancestors[anchor_id] & selected_set:
                masked.append(anchor_id)
            else:
                selected.append(anchor_id)
                selected_set.add(anchor_id)
        output.append(AnchorSelection(route.assertion_id, route.region, tuple(selected), tuple(masked)))
    return tuple(output)


def derive_anchor_updates(
    frozen: FrozenTopologyInput,
    assertions: tuple[InterviewAssertion, ...],
    selections: tuple[AnchorSelection, ...],
) -> tuple[AnchorUpdate, ...]:
    """内部阶段 3：从断言产生可审计更新意图，不修改任何节点状态。"""
    assertion_by_id = {item.assertion_id: item for item in assertions}
    if len(assertion_by_id) != len(assertions):
        raise ValueError("topology_duplicate_assertion_id")
    nodes = _nodes(frozen)
    _graph(frozen, nodes)
    updates: list[AnchorUpdate] = []
    for selection in selections:
        assertion = assertion_by_id.get(selection.assertion_id)
        if assertion is None:
            raise ValueError("topology_assertion_missing")
        for anchor_id in selection.selected_anchor_ids:
            if anchor_id not in nodes:
                raise ValueError("topology_anchor_not_in_frozen_snapshot")
            updates.append(AnchorUpdate(
                assertion_id=assertion.assertion_id, anchor_id=anchor_id,
                # 逐锚点判断来自同一面评单元的一次全量评估；未单列时使用单元默认结论。
                judgement=assertion.candidate_judgements.get(anchor_id, assertion.judgement), level=assertion.level,
                source_quote=assertion.source_quote, reason=assertion.text,
                proof_key=assertion.scenario_id or assertion.assertion_id,
                score=assertion.score,
                target_type=str(nodes[anchor_id].get("targetType") or nodes[anchor_id].get("anchorType") or ""),
                source_refs=assertion.source_refs,
            ))
    return tuple(updates)


def _observed_score(update: AnchorUpdate) -> float | None:
    """依据冻结锚点规则得到观察分；LLM 只给 judgement，不直接给最终分数。"""
    if update.score is not None:
        return max(0.0, min(1.0, float(update.score)))
    if update.level is not None:
        return LEVEL_SCORE.get(max(0, min(5, int(update.level))))
    # LLM 只返回离散 judgement。观察分是版本化算法规则，不能让模型自由给分；
    # not_support 表示无关/证据不足，因此不产生更新。
    return JUDGEMENT_OBSERVATION_SCORE.get(update.judgement)


def apply_updates_and_aggregate(
    input_data: TopologyScoringInput, updates: tuple[AnchorUpdate, ...],
) -> TopologyScoringResult:
    """阶段 4：应用锚点更新，再沿冻结图向上聚合。

    每个节点先按 ``proof_key`` 汇总本轮独立证据，调用公共的
    ``aggregate_target_round_results`` 与 ``apply_round_update``；绝不把每条
    judgement 直接累加为固定分数。随后仅把直接更新的净变化带入父节点聚合，
    因而同一面评单元已被屏蔽的低层节点不会重复计分。
    """
    nodes = _nodes(input_data.frozen)
    children, _, postorder = _graph(input_data.frozen, nodes)
    state_by_id = {node_id: dict(row) for node_id, row in nodes.items()}
    before_score = {node_id: float(row.get("score") or 0.0) for node_id, row in state_by_id.items()}
    evidence_by_anchor: dict[str, list[dict[str, Any]]] = defaultdict(list)
    update_refs: dict[str, list[str]] = defaultdict(list)

    for update in updates:
        if update.anchor_id not in state_by_id:
            raise ValueError("topology_anchor_not_in_frozen_snapshot")
        observed_score = _observed_score(update)
        if observed_score is None:
            # 没有冻结 Rubric/等级的判断只保留在审计 Artifact，不能伪造分数更新。
            continue
        proof_key = update.proof_key or update.assertion_id
        evidence_by_anchor[update.anchor_id].append({
            "result_id": f"TOPOLOGY_{update.assertion_id}_{update.anchor_id}",
            "observation_id": update.assertion_id,
            "target_type": update.target_type or str(nodes[update.anchor_id].get("anchorType") or "anchor"),
            "target_id": update.anchor_id,
            "score": observed_score,
            "interviewer_judgement": update.judgement.value,
            "scenario_id": proof_key,
            "source_refs": [dict(item) for item in update.source_refs],
        })
        update_refs[update.anchor_id].append(update.assertion_id)

    direct_net_delta: dict[str, float] = {}
    for anchor_id, rows in evidence_by_anchor.items():
        round_evidence, _risk = aggregate_target_round_results(
            rows,
            interview_round_id="TOPOLOGY_CURRENT",
            target_type=str(rows[0]["target_type"]),
            target_id=anchor_id,
        )
        round_update = apply_round_update(
            before_score[anchor_id],
            round_evidence,
            max_negative_delta=POST_INTERVIEW_MAX_NEGATIVE_DELTA,
        )
        direct_net_delta[anchor_id] = float(round_update["net_delta"])

    # 子节点先完成聚合。拓扑只保存结构关系，不保存 V1 每一层各自的聚合权重，
    # 因此这里只传播“本轮变化量”，不能把子节点绝对分简单平均后覆盖父节点基线。
    # 这样未受影响节点严格保持 V1/V2 原值，直接观察和子节点变化才会进入本轮结果。
    propagated_refs: dict[str, list[str]] = defaultdict(list)
    for node_id in postorder:
        child_ids = children.get(node_id, ())
        child_delta = (
            sum(
                float(state_by_id[child_id].get("score") or 0.0)
                - before_score[child_id]
                for child_id in child_ids
            )
            / len(child_ids)
            if child_ids
            else 0.0
        )
        after = max(
            0.0,
            min(
                1.0,
                before_score[node_id]
                + child_delta
                + direct_net_delta.get(node_id, 0.0),
            ),
        )
        state_by_id[node_id]["score"] = after
        if node_id in direct_net_delta:
            state_by_id[node_id]["state"] = "updated"
        propagated_refs[node_id] = list(
            dict.fromkeys(
                [
                    *update_refs.get(node_id, ()),
                    *(
                        assertion_id
                        for child_id in child_ids
                        for assertion_id in propagated_refs.get(child_id, ())
                    ),
                ]
            )
        )

    anchor_changes = [
        {
            "anchorId": node_id,
            "before": before_score[node_id],
            "after": state_by_id[node_id]["score"],
            "delta": state_by_id[node_id]["score"] - before_score[node_id],
            "assertionIds": propagated_refs.get(node_id, []),
        }
        for node_id in sorted(nodes)
        if node_id in update_refs
        or abs(state_by_id[node_id]["score"] - before_score[node_id]) > 1e-12
    ]
    previous = dict(input_data.frozen.previous_core_result)
    core = _project_topology_result(previous, nodes, state_by_id, anchor_changes)
    core["topologyStates"] = {
        key: {
            "score": value.get("score"),
            "level": value.get("level"),
            "state": value.get("state"),
        }
        for key, value in state_by_id.items()
    }
    return TopologyScoringResult(routes=(), selections=(), updates=updates, core_result=core)


def _project_topology_result(
    previous: Mapping[str, Any],
    nodes: Mapping[str, Mapping[str, Any]],
    states: Mapping[str, Mapping[str, Any]],
    anchor_changes: list[dict[str, Any]],
) -> dict[str, Any]:
    """Project changed topology roots back into the published assessment contract."""

    core = deepcopy(dict(previous))
    experience = deepcopy(dict(previous.get("experience_result") or {}))
    job = deepcopy(dict(previous.get("job_result") or {}))
    _apply_referenced_scores(
        experience.get("candidate_framework_results"),
        id_field="framework_id",
        node_type="candidate_framework",
        reference_kind="framework",
        nodes=nodes,
        states=states,
    )
    _apply_referenced_scores(
        job.get("capability_results"),
        id_field="job_capability_id",
        node_type="job_capability",
        reference_kind="job_capability",
        nodes=nodes,
        states=states,
    )
    _apply_referenced_scores(
        job.get("job_unit_results"),
        id_field="job_unit_id",
        node_type="job_unit",
        reference_kind="job_unit",
        nodes=nodes,
        states=states,
    )

    previous_score = dict(previous.get("score_result") or {})
    frameworks = _mapping_rows(experience.get("candidate_framework_results"))
    units = _mapping_rows(job.get("job_unit_results"))
    experience_score = (
        aggregate_resume_score(frameworks)
        if frameworks
        else _number(previous_score.get("experience"), 0.0)
    )
    job_score = (
        round(_aggregate_job(units) * 100.0, 2)
        if any(item.get("aggregation_role") == "required" for item in units)
        else _number(previous_score.get("job_fit"), 0.0)
    )
    education = deepcopy(dict(previous.get("education_result") or {}))
    education_score = _number(previous_score.get("education"), 0.0)
    _, engine = score_candidate_profile(
        {
            "stage": "incremental",
            "preset_experience_result": experience,
            "job_result": job,
            "education_result": {**education, "score": education_score},
            "score_summary": {
                "job_requirement_score": job_score,
                "resume_experience_score": experience_score,
            },
        }
    )
    score_result = {
        **previous_score,
        "total": engine["base_score"],
        "job_fit": engine["job_capability_fit_score"],
        "experience": engine["resume_experience_score"],
        "education": engine["education_background_score"],
        "score_engine_version": engine["score_engine_version"],
        "incrementalChanges": anchor_changes,
        "updatedAnchorCount": len(anchor_changes),
    }
    score_changes = [
        {
            "metric": metric,
            "previous_value": _optional_number(previous_score.get(metric)),
            "current_value": _optional_number(score_result.get(metric)),
            "delta": _delta(
                _optional_number(previous_score.get(metric)),
                _optional_number(score_result.get(metric)),
            ),
        }
        for metric in ("total", "job_fit", "experience", "education")
        if _optional_number(previous_score.get(metric))
        != _optional_number(score_result.get(metric))
    ]
    capability_changes = _job_capability_changes(nodes, states, anchor_changes)
    experience.setdefault("score_summary", {})["resume_experience_score"] = experience_score
    job.setdefault("score_summary", {}).update(
        {
            "job_requirement_score": job_score,
            "job_capability_fit_score": job_score,
        }
    )
    core.update(
        {
            "experience_result": experience,
            "job_result": job,
            "education_result": education,
            "score_result": score_result,
            "score_changes": score_changes,
            "capability_changes": capability_changes,
            "affected_result_refs": [
                str(item["anchorId"]) for item in anchor_changes
            ],
        }
    )
    return core


def _apply_referenced_scores(
    rows: Any,
    *,
    id_field: str,
    node_type: str,
    reference_kind: str,
    nodes: Mapping[str, Mapping[str, Any]],
    states: Mapping[str, Mapping[str, Any]],
) -> None:
    indexed = {
        str(item.get(id_field) or ""): item for item in _mapping_rows(rows)
    }
    for node_id, node in nodes.items():
        if str(node.get("anchorType") or node.get("anchor_type") or "") != node_type:
            continue
        reference_id = _reference_id(node, reference_kind)
        row = indexed.get(reference_id)
        if row is not None and node_id in states:
            row["score"] = float(states[node_id].get("score") or 0.0)


def _job_capability_changes(
    nodes: Mapping[str, Mapping[str, Any]],
    states: Mapping[str, Mapping[str, Any]],
    anchor_changes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    changes_by_id = {str(item["anchorId"]): item for item in anchor_changes}
    output: list[dict[str, Any]] = []
    for node_id, node in nodes.items():
        if str(node.get("anchorType") or "") != "job_capability":
            continue
        change = changes_by_id.get(node_id)
        capability_id = _reference_id(node, "job_capability")
        if change is None or not capability_id:
            continue
        output.append(
            {
                "result_ref": f"job_capability:{capability_id}",
                "capability_id": capability_id,
                "previous_score": change["before"],
                "current_score": states[node_id].get("score"),
                "delta": change["delta"],
                "reason_code": "interview_anchor_judgement",
                "evidence_ids": list(change.get("assertionIds") or ()),
            }
        )
    return output


def _reference_id(node: Mapping[str, Any], kind: str) -> str:
    for reference in node.get("references") or ():
        if isinstance(reference, Mapping) and str(
            reference.get("kind") or reference.get("reference_kind") or ""
        ) == kind:
            return str(reference.get("id") or reference.get("reference_id") or "")
    return ""


def _mapping_rows(value: Any) -> list[dict[str, Any]]:
    return [item for item in value or () if isinstance(item, dict)]


def _optional_number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _number(value: Any, default: float) -> float:
    current = _optional_number(value)
    return current if current is not None else default


def _delta(before: float | None, after: float | None) -> float | None:
    if before is None or after is None:
        return None
    return round(after - before, 6)

