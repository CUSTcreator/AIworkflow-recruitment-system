"""初筛 V1 评分拓扑的规范化发布服务。

本模块位于业务发布层，不参与评分算法。它把评分核心已经计算完成的拓扑投影为
关系型行，并与 ApplicationAssessmentVersion 在同一短事务内发布。V2/V3 只读取
这些冻结行；不得从 ResumeProfile 或 JobRequirementProfile 重新发现节点或边。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Protocol

from backend.app.models.entities import (
    ApplicationAssessmentVersion,
    AssessmentTopologyDefinition,
    AssessmentTopologyEdge,
    AssessmentTopologyNode,
    AssessmentTopologyNodeReference,
    AssessmentTopologyNodeState,
    AssessmentTopologySnapshot,
)


class _SessionWriter(Protocol):
    """发布服务所需的最小 Session 能力，方便对写入计划进行无数据库单元测试。"""

    def add(self, instance: Any) -> None: ...


@dataclass(frozen=True)
class TopologyReferencePlan:
    """一个节点对结构化简历或岗位事实的稳定引用。"""

    reference_role: str
    reference_kind: str
    reference_id: str


@dataclass(frozen=True)
class TopologyNodePlan:
    """V1 中一个已经激活的评分锚点及其首份状态。"""

    anchor_key: str
    anchor_type: str
    ordinal: int
    score_value: float | None
    level_value: float | None
    state: str
    references: tuple[TopologyReferencePlan, ...]


@dataclass(frozen=True)
class TopologyEdgePlan:
    """一个固定的子节点到父节点的聚合关系。"""

    child_anchor_key: str
    parent_anchor_key: str
    relation_type: str


@dataclass(frozen=True)
class V1TopologyPersistencePlan:
    """可直接持久化的 V1 拓扑计划。

    ``structure_hash`` 不含分数；``state_hash`` 仅描述本次首份节点状态。两者分开
    后，V2/V3 的面评增量只能改变状态哈希，不能改变 V1 固化的结构哈希。
    """

    definition_schema_version: str
    state_schema_version: str
    source_core_hash: str
    structure_hash: str
    state_hash: str
    nodes: tuple[TopologyNodePlan, ...]
    edges: tuple[TopologyEdgePlan, ...]


def build_v1_persistence_plan(topology: Mapping[str, Any]) -> V1TopologyPersistencePlan:
    """把 V1 内存拓扑转换为不含 JSON 业务包的规范化写入计划。

    输入只能来自 ``build_v1_topology_snapshot`` 的当轮评分结果；本函数不查询数据库、
    不补齐节点，也不基于文本猜测新的证据关系。任何不带稳定 anchorId 的结果会被忽略，
    防止 V2/V3 后续面对无法稳定定位的临时节点。
    """
    raw_anchors = _mapping_list(topology.get("anchors"))
    raw_relations = _mapping_list(topology.get("relations"))
    nodes: list[TopologyNodePlan] = []
    known_keys: set[str] = set()

    for ordinal, anchor in enumerate(sorted(raw_anchors, key=lambda item: _text(item.get("anchorId")))):
        anchor_key = _text(anchor.get("anchorId"))
        anchor_type = _text(anchor.get("anchorType"))
        if not anchor_key or not anchor_type or anchor_key in known_keys:
            continue
        known_keys.add(anchor_key)
        source = _mapping(anchor.get("source"))
        nodes.append(
            TopologyNodePlan(
                anchor_key=anchor_key,
                anchor_type=anchor_type,
                ordinal=ordinal,
                score_value=_number(source.get("score")),
                level_value=_number(source.get("level")),
                state="inactive" if source.get("effective") is False else "active",
                references=tuple(_reference_plans(source)),
            )
        )

    edges = tuple(
        TopologyEdgePlan(
            child_anchor_key=child_key,
            parent_anchor_key=parent_key,
            relation_type=relation_type,
        )
        for relation in raw_relations
        if (child_key := _text(relation.get("childAnchorId"))) in known_keys
        and (parent_key := _text(relation.get("parentAnchorId"))) in known_keys
        and child_key != parent_key
        and (relation_type := _text(relation.get("relation")))
    )

    definition_schema_version = "assessment_topology_definition_v1"
    state_schema_version = "assessment_topology_state_v1"
    structure_payload = {
        "schemaVersion": definition_schema_version,
        "nodes": [
            {
                "anchorKey": node.anchor_key,
                "anchorType": node.anchor_type,
                "references": [
                    {
                        "role": reference.reference_role,
                        "kind": reference.reference_kind,
                        "id": reference.reference_id,
                    }
                    for reference in node.references
                ],
            }
            for node in nodes
        ],
        "edges": [
            {
                "child": edge.child_anchor_key,
                "parent": edge.parent_anchor_key,
                "relation": edge.relation_type,
            }
            for edge in edges
        ],
    }
    state_payload = {
        "schemaVersion": state_schema_version,
        "nodes": [
            {
                "anchorKey": node.anchor_key,
                "score": node.score_value,
                "level": node.level_value,
                "state": node.state,
            }
            for node in nodes
        ],
    }
    return V1TopologyPersistencePlan(
        definition_schema_version=definition_schema_version,
        state_schema_version=state_schema_version,
        source_core_hash=_text(topology.get("sourceCoreHash")) or _stable_hash({}),
        structure_hash=_stable_hash(structure_payload),
        state_hash=_stable_hash(state_payload),
        nodes=tuple(nodes),
        edges=edges,
    )


class V1TopologyPersistenceService:
    """将 V1 的规范化拓扑行加入当前评估发布事务。

    调用方不得在本服务内 ``commit``。初筛发布 Step 的 ``StepRunner`` 才是这批
    ApplicationAssessmentVersion、Definition、Snapshot、NodeState 的唯一提交者。
    """

    def __init__(self, db: _SessionWriter, *, id_factory: Callable[[str], str]) -> None:
        self.db = db
        self.id_factory = id_factory

    def persist(
        self,
        *,
        assessment: ApplicationAssessmentVersion,
        application_id: str,
        topology: Mapping[str, Any],
        plan: V1TopologyPersistencePlan | None = None,
    ) -> AssessmentTopologySnapshot:
        """写入 V1 定义、首份 Snapshot 与所有节点状态，保持调用方事务原子性。"""
        plan = plan or build_v1_persistence_plan(topology)
        definition = AssessmentTopologyDefinition(
            topology_definition_id=self.id_factory("ATD"),
            root_assessment_version_id=assessment.assessment_version_id,
            application_id=application_id,
            structure_schema_version=plan.definition_schema_version,
            structure_hash=plan.structure_hash,
        )
        snapshot = AssessmentTopologySnapshot(
            topology_snapshot_id=self.id_factory("ATS"),
            assessment_version_id=assessment.assessment_version_id,
            application_id=application_id,
            topology_definition_id=definition.topology_definition_id,
            previous_topology_snapshot_id=None,
            state_schema_version=plan.state_schema_version,
            source_core_hash=plan.source_core_hash,
            state_hash=plan.state_hash,
            # 旧列因既有数据库迁移仍为非空。本服务不再写入拓扑数据本体。
            schema_version=plan.definition_schema_version,
            topology_hash=plan.structure_hash,
            topology_json={},
        )
        self.db.add(definition)
        self.db.add(snapshot)
        # 拓扑节点和快照都依赖定义；先 flush 父记录，后续节点/边才能安全引用已存在的 ID。
        self.db.flush()

        node_ids: dict[str, str] = {}
        for node_plan in plan.nodes:
            node = AssessmentTopologyNode(
                topology_node_id=self.id_factory("ATN"),
                topology_definition_id=definition.topology_definition_id,
                anchor_key=node_plan.anchor_key,
                anchor_type=node_plan.anchor_type,
                ordinal=node_plan.ordinal,
            )
            node_ids[node_plan.anchor_key] = node.topology_node_id
            self.db.add(node)
            self.db.add(
                AssessmentTopologyNodeState(
                    topology_node_state_id=self.id_factory("ATNS"),
                    topology_snapshot_id=snapshot.topology_snapshot_id,
                    topology_node_id=node.topology_node_id,
                    score_value=node_plan.score_value,
                    level_value=node_plan.level_value,
                    state=node_plan.state,
                )
            )
            for reference in node_plan.references:
                self.db.add(
                    AssessmentTopologyNodeReference(
                        topology_node_reference_id=self.id_factory("ATNR"),
                        topology_node_id=node.topology_node_id,
                        reference_role=reference.reference_role,
                        reference_kind=reference.reference_kind,
                        reference_id=reference.reference_id,
                    )
                )

        # 边表通过外键引用节点表，必须先 flush 全部节点。
        self.db.flush()

        for edge_plan in plan.edges:
            self.db.add(
                AssessmentTopologyEdge(
                    topology_edge_id=self.id_factory("ATE"),
                    topology_definition_id=definition.topology_definition_id,
                    parent_node_id=node_ids[edge_plan.parent_anchor_key],
                    child_node_id=node_ids[edge_plan.child_anchor_key],
                    relation_type=edge_plan.relation_type,
                )
            )
        self.db.flush()
        return snapshot

    def persist_successor(
        self,
        *,
        assessment: ApplicationAssessmentVersion,
        application_id: str,
        previous_topology: Mapping[str, Any],
        topology_core_result: Mapping[str, Any],
    ) -> AssessmentTopologySnapshot:
        """在 V2/V3 发布事务中复制冻结结构并写入本轮节点状态。

        V2/V3 严禁从简历、岗位画像或展示 JSON 重新发现评分节点。唯一允许的输入是
        步骤 1 固化的 ``previous_topology`` 与步骤 7 产出的 ``topologyStates``；因此
        新 Snapshot 与前一版本共享 Definition 和 Node，只有节点状态与版本链发生变化。
        """
        definition_id = _text(previous_topology.get("definitionId"))
        previous_snapshot_id = _text(previous_topology.get("snapshotId"))
        node_rows = _mapping_list(previous_topology.get("nodes"))
        if not definition_id or not previous_snapshot_id or not node_rows:
            raise RuntimeError("post_interview_topology_snapshot_contract_invalid")

        updates = _mapping(topology_core_result.get("topologyStates"))
        state_rows: list[tuple[str, float | None, float | None, str]] = []
        for node in node_rows:
            node_id = _text(node.get("nodeId"))
            if not node_id:
                raise RuntimeError("post_interview_topology_node_id_missing")
            updated = _mapping(updates.get(node_id))
            state_rows.append((
                node_id,
                _number(updated.get("score")) if updated else _number(node.get("score")),
                _number(updated.get("level")) if updated else _number(node.get("level")),
                _text(updated.get("state")) if updated else _text(node.get("state")),
            ))

        state_payload = {
            "schemaVersion": _text(previous_topology.get("stateSchemaVersion")) or "assessment_topology_state_v1",
            "nodes": [
                {"nodeId": node_id, "score": score, "level": level, "state": state}
                for node_id, score, level, state in state_rows
            ],
        }
        snapshot = AssessmentTopologySnapshot(
            topology_snapshot_id=self.id_factory("ATS"),
            assessment_version_id=assessment.assessment_version_id,
            application_id=application_id,
            topology_definition_id=definition_id,
            previous_topology_snapshot_id=previous_snapshot_id,
            state_schema_version=state_payload["schemaVersion"],
            source_core_hash=_text(topology_core_result.get("sourceCoreHash")) or _stable_hash(topology_core_result),
            state_hash=_stable_hash(state_payload),
            # 旧 JSON 列仍为非空兼容列；新的运行时绝不从这里读取拓扑。
            schema_version=_text(previous_topology.get("structureSchemaVersion")) or "assessment_topology_definition_v1",
            topology_hash=_text(previous_topology.get("structureHash")) or _stable_hash({}),
            topology_json={},
        )
        self.db.add(snapshot)
        # V2/V3 节点状态通过外键引用本轮快照，先 flush 快照再写状态。
        self.db.flush()
        for node_id, score, level, state in state_rows:
            self.db.add(AssessmentTopologyNodeState(
                topology_node_state_id=self.id_factory("ATNS"),
                topology_snapshot_id=snapshot.topology_snapshot_id,
                topology_node_id=node_id,
                score_value=score,
                level_value=level,
                state=state or "active",
            ))
        # 状态行写入后立即检查外键和唯一约束，发布器随后再统一提交事务。
        self.db.flush()
        return snapshot


def _reference_plans(source: Mapping[str, Any]) -> Iterable[TopologyReferencePlan]:
    """只提取稳定业务标识；分数、等级和原始文本不进入引用表。"""
    values: set[tuple[str, str, str]] = set()
    for field, value in source.items():
        if not field.endswith("_id"):
            continue
        identifier = _text(value)
        if identifier:
            values.add((field.removesuffix("_id"), field.removesuffix("_id"), identifier))

    evidence_id = _text(source.get("evidence_id"))
    evidence_type = _text(source.get("evidence_type"))
    if evidence_id:
        values.add(("evidence", evidence_type or "unknown_evidence", evidence_id))
    evidence_ref = _text(source.get("evidence_ref"))
    if evidence_ref:
        values.add(("evidence_ref", "evidence_ref", evidence_ref))

    return (
        TopologyReferencePlan(reference_role=role, reference_kind=kind, reference_id=identifier)
        for role, kind, identifier in sorted(values)
    )


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _mapping_list(value: Any) -> list[Mapping[str, Any]]:
    return [item for item in (value or []) if isinstance(item, Mapping)]


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _text(value: Any) -> str:
    return str(value or "").strip()


def _stable_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()