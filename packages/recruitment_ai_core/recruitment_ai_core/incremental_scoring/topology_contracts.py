"""V2/V3 面评增量评分的拓扑合同。

本文件是后续实现的硬边界。它不依赖 ORM、数据库或 Workflow：算法包只接收
已冻结的上一版评分图与本轮面评断言，输出可审计的 AnchorUpdate 草稿。

禁止事项：不得从 ResumeProfile/JD 重新发现证据、不得新增 Pair/指标/能力节点，
不得把 Application/Candidate 等业务身份字段传入纯算法。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping, Sequence


Json = Mapping[str, Any]


class ScoringRegion(StrEnum):
    EXPERIENCE = "experience"
    JOB = "job"
    BOTH = "both"
    NON_SCORING = "non_scoring"


class AnchorJudgement(StrEnum):
    SUPPORT = "support"
    VERIFIED = "verified"
    PARTIAL = "partial"
    NOT_SUPPORT = "not_support"
    WEAK_CONTRADICTION = "weak_contradiction"
    CONTRADICTED = "contradicted"
    STRONG_CONTRADICTION = "strong_contradiction"


@dataclass(frozen=True, slots=True)
class FrozenTopologyInput:
    """V2/V3 唯一允许读取的评分图。

    ``topology`` 必须是 V1 发布时生成的 AssessmentTopologySnapshot；V3 仍沿用
    同一张 V1 图，而不是从 V2 结果重新发现节点。``previous_core_result`` 只提供
    当前基线分数，不能扩大拓扑中的可选范围。
    """

    topology: Json
    previous_core_result: Json


@dataclass(frozen=True, slots=True)
class InterviewAssertion:
    """面评语义解析后的最小可评分断言；一条断言可路由到多个独立区域。"""

    assertion_id: str
    text: str
    source_quote: str
    judgement: AnchorJudgement
    level: int | None = None
    candidate_anchor_ids: Sequence[str] = field(default_factory=tuple)
    # 一个面评单元可对不同锚点给出不同结论。键必须属于 candidate_anchor_ids；
    # 这使得筛选先在完整路径上做“高层屏蔽”，再保留每个锚点自己的 judgement。
    candidate_judgements: Mapping[str, AnchorJudgement] = field(default_factory=dict)
    region: ScoringRegion | None = None
    # 证明足迹和观察分由程序从冻结记录/Rubric 补齐，LLM 不负责生成。
    scenario_id: str | None = None
    source_refs: Sequence[Json] = field(default_factory=tuple)
    score: float | None = None


@dataclass(frozen=True, slots=True)
class RegionRoute:
    """断言进入某个评分区域后的候选锚点集合，仍未决定最终命中。"""

    assertion_id: str
    region: ScoringRegion
    candidate_anchor_ids: Sequence[str]


@dataclass(frozen=True, slots=True)
class AnchorSelection:
    """一个断言在单一分支上最终保留的非重叠锚点。"""

    assertion_id: str
    region: ScoringRegion
    selected_anchor_ids: Sequence[str]
    masked_anchor_ids: Sequence[str] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class AnchorUpdate:
    """对冻结图中一个已有锚点的分数增量草稿。

    ``anchor_id`` 必须属于 ``FrozenTopologyInput.topology.anchors``。更新应用后只能
    沿 ``relations`` 向父节点聚合；总分、推荐结论和展示文案都不是可被 LLM 直接命中的锚点。
    """

    assertion_id: str
    anchor_id: str
    judgement: AnchorJudgement
    level: int | None
    source_quote: str
    reason: str
    # 同一 proof_key 在同一锚点只算一次；score 缺失时由 level 映射。
    proof_key: str | None = None
    score: float | None = None
    target_type: str | None = None
    source_refs: Sequence[Json] = field(default_factory=tuple)

@dataclass(frozen=True, slots=True)
class TopologyScoringInput:
    """拓扑增量核心唯一输入；不含任何数据库实体或身份字段。"""

    frozen: FrozenTopologyInput
    assertions: Sequence[InterviewAssertion]


@dataclass(frozen=True, slots=True)
class TopologyScoringResult:
    """拓扑更新完成后的纯算法输出；发布层再将其写入 AAV。"""

    routes: Sequence[RegionRoute]
    selections: Sequence[AnchorSelection]
    updates: Sequence[AnchorUpdate]
    core_result: Json


