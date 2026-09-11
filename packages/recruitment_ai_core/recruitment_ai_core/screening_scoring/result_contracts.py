from __future__ import annotations

"""初筛阶段的显式运行时契约，不对应数据库表。"""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ScoringCoreInput:
    """冻结的评分来源，不含 application/candidate 等业务写入上下文。"""
    resume_profile: dict[str, Any]
    job_profile: dict[str, Any]
    education_ranking_entries: list[dict[str, Any]]
    ranking_dataset_version: str
    llm_config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ScreeningCoreResult:
    """经历、岗位、学历和分数原始结果，不含推荐或展示文案。"""
    resume_profile: dict[str, Any]
    job_profile: dict[str, Any]
    preset_experience_result: dict[str, Any]
    job_result: dict[str, Any]
    education_result: dict[str, Any]
    score_result: dict[str, Any]
    evidence_index: dict[str, Any]
    # 能力图谱保留计算层级、版本与可追溯结果；不是数据库实体。
    capability_graph: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RuleDerivedResult:
    """确定性业务规则产物，LLM 只能引用、解释和归并这些事实。"""
    hard_screening_result: dict[str, Any]
    strength_signals: list[dict[str, Any]]
    weakness_signals: list[dict[str, Any]]
    # 初筛规则层产生的跨阶段核验目标更新。发布器将其原子写入 InterviewTarget 表，
    # AAV 仍保留同一快照用于审计与重放。
    target_updates: list[dict[str, Any]]


@dataclass(frozen=True, slots=True)
class PresentationResult:
    """面向 HR 的展示文案；模型不可用时用规则降级生成同形结果。"""
    recommendation_level: str
    recommendation_reason: str
    strengths: list[dict[str, Any]]
    weaknesses: list[dict[str, Any]]
    verification_focus: list[dict[str, Any]]
    generation_mode: str
    generated_at: str
    source_input_hash: str
    # 单次 Bundle 调用生成推荐与全部展示文案；模式说明是否使用确定性降级。
    summary_generation_mode: str = "rule_fallback"
    verification_focus_generation_mode: str = "rule_fallback"








