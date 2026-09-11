"""评分来源可用性判定骨架。

该服务只聚合已完成的 Activity 质量，不读取数据库、不调用 LLM、不生成能力模型。
V1/V2/V3 的冻结步骤在接入后用它判断：当前 ResumeProfile、JobRequirementProfile 与
必要证据是否足以进入评分。技术降级不会自动变成零分；缺少最低覆盖时应当转成可
由用户处理的 blocked，而不是让整条招聘申请进入不可恢复失败。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from backend.app.shared.workflows import ActivityOutcomeKind


@dataclass(frozen=True, slots=True)
class SourceQuality:
    """一个已冻结来源或子活动的可用性事实。"""

    source_key: str
    usable: bool
    outcome_kind: ActivityOutcomeKind = ActivityOutcomeKind.COMPLETED
    reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class SourceReadiness:
    """供 Workflow 映射为 StepOutcome 的稳定判定结果。"""

    outcome_kind: ActivityOutcomeKind
    usable_source_keys: tuple[str, ...]
    degraded_source_keys: tuple[str, ...]
    missing_source_keys: tuple[str, ...]
    resolution_code: str


class SourceReadinessService:
    """把“来源数量是否够用”与“质量是否降级”分开判定。

    这是规则骨架，不在此处写 Resume、JD 或评分的领域名称。各流程仅传入已冻结的
    事实和最低必需来源集合，避免把来源选择逻辑分散到 LLM Activity 中。
    """

    def assess(
        self,
        sources: Iterable[SourceQuality],
        *,
        required_source_keys: Iterable[str],
    ) -> SourceReadiness:
        items = tuple(sources)
        required = tuple(dict.fromkeys(required_source_keys))
        by_key = {item.source_key: item for item in items}
        missing = tuple(
            key for key in required
            if key not in by_key or not by_key[key].usable or by_key[key].outcome_kind == ActivityOutcomeKind.BLOCKED
        )
        usable = tuple(item.source_key for item in items if item.usable)
        degraded = tuple(
            item.source_key for item in items
            if item.usable and item.outcome_kind == ActivityOutcomeKind.DEGRADED
        )
        if missing:
            return SourceReadiness(
                outcome_kind=ActivityOutcomeKind.BLOCKED,
                usable_source_keys=usable,
                degraded_source_keys=degraded,
                missing_source_keys=missing,
                resolution_code="source_minimum_coverage_missing",
            )
        return SourceReadiness(
            outcome_kind=(ActivityOutcomeKind.DEGRADED if degraded else ActivityOutcomeKind.COMPLETED),
            usable_source_keys=usable,
            degraded_source_keys=degraded,
            missing_source_keys=(),
            resolution_code=("source_quality_degraded" if degraded else "source_ready"),
        )