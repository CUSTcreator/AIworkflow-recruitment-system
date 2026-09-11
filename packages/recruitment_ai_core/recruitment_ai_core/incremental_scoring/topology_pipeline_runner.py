"""冻结拓扑增量评分 Pipeline 的统一纯数据入口。

Workflow 只调用 ``run_topology_incremental_scoring``；本 runner 按固定顺序串联路由、
最高层锚点筛选、AnchorUpdate 生成、更新应用和拓扑聚合，并提供 ``topology_result_as_dict``
将结果序列化为 Workflow Artifact。它不访问数据库，也不负责规则派生、展示加工或正式发布。
"""
from __future__ import annotations

from dataclasses import asdict
from collections.abc import Mapping, Sequence
from typing import Any

from .topology_contracts import FrozenTopologyInput, InterviewAssertion, TopologyScoringInput, TopologyScoringResult
from .topology_pipeline import apply_updates_and_aggregate, derive_anchor_updates, route_assertions_to_regions, select_non_overlapping_anchors


def run_topology_incremental_scoring(
    *, frozen_topology: Mapping[str, Any], previous_core_result: Mapping[str, Any],
    assertions: Sequence[InterviewAssertion],
) -> TopologyScoringResult:
    """按目标顺序执行拓扑评分的四个内部阶段。"""
    frozen = FrozenTopologyInput(topology=dict(frozen_topology), previous_core_result=dict(previous_core_result))
    assertion_tuple = tuple(assertions)
    # 这里的 route 是拓扑区域路由，不是经历侧/岗位侧路由；侧判断已在 Workflow
    # 的 evaluate_anchor_judgements Step 中完成，且岗位侧始终执行。
    routes = route_assertions_to_regions(frozen, assertion_tuple)
    selections = select_non_overlapping_anchors(frozen, routes)
    updates = derive_anchor_updates(frozen, assertion_tuple, selections)
    applied = apply_updates_and_aggregate(TopologyScoringInput(frozen=frozen, assertions=assertion_tuple), tuple(updates))
    return TopologyScoringResult(routes=routes, selections=selections, updates=applied.updates, core_result=applied.core_result)


def topology_result_as_dict(result: TopologyScoringResult) -> dict[str, Any]:
    return {
        "routes": [asdict(item) for item in result.routes],
        "selections": [asdict(item) for item in result.selections],
        "updates": [asdict(item) for item in result.updates],
        "coreResult": dict(result.core_result),
    }
