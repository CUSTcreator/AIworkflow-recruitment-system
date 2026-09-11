"""一面题单五段流程的运行时结果契约。

这些 dataclass 都只在 Workflow 内传递。发布器会分别将冻结来源、约束与提案、规则结果、
前端展示 DTO 写入同一个 ``FirstInterviewPlanVersion`` 的命名 JSON 区块。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class QuestionPlanningConstraintSet:
    """纯程序生成的题单约束集，不含任何自然语言题干。

    target_snapshots 固定目标、能力叶子、证据和基础 Rubric；question_slots 固定每一题的 slot_id、
    Target 绑定、题型、核验角度和优先级；generation_warnings 仅说明约束构建时的降级原因。
    """

    target_snapshots: list[dict[str, Any]] = field(default_factory=list)
    question_slots: list[dict[str, Any]] = field(default_factory=list)
    generation_warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class QuestionDraftProposal:
    """LLM 对一个既定槽位的题目提案。

    ``slot_id`` 是规则层提前生成的约束 ID；LLM 只能填写题干、追问、期望证据和 Rubric 文案，
    无权改变题目绑定的 Target、能力叶子或题目类型。
    """

    slot_id: str
    question_text: str
    follow_up_questions: list[str] = field(default_factory=list)
    expected_evidence: list[str] = field(default_factory=list)
    evaluation_rubrics: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class QuestionProposalGenerationResult:
    """LLM 题单核心产出及其可观测信息。

    proposals 中每一项必须通过 slot_id 回连既定 QuestionSlot；generation_mode 标记 llm 或
    rule_fallback；llm_trace 仅用于审计和故障排查，绝不能作为业务规则输入。
    """

    proposals: list[QuestionDraftProposal] = field(default_factory=list)
    generation_mode: str = "rule_fallback"
    generation_warnings: list[str] = field(default_factory=list)
    llm_trace: dict[str, Any] | None = None


@dataclass(slots=True)
class QuestionRuleDerivationResult:
    """规则校验后的可执行题单草稿。

    validated_questions 是唯一可确认的业务题目：已含稳定题目 ID、Target/证据/能力绑定、题型、
    场景、Rubric 和展示内容；coverage 是程序计算的覆盖率，不采用 LLM 自报值。
    """

    validated_questions: list[dict[str, Any]] = field(default_factory=list)
    recommended_question_ids: list[str] = field(default_factory=list)
    coverage: dict[str, Any] = field(default_factory=dict)
    generation_warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class FirstInterviewPlanPresentationResult:
    """仅供一面题单确认页读取的展示 DTO。

    draft_guide 和 question_suggestions 使用 camelCase 前端字段；它们只能由展示层生成，不能倒灌
    回纯算法或规则层。generation_warnings 会随 DTO 返回给页面，供用户理解空题单或降级原因。
    """

    planning_summary: str = ""
    question_suggestions: list[dict[str, Any]] = field(default_factory=list)
    draft_guide: dict[str, Any] = field(default_factory=dict)
    generation_mode: str = "rule_fallback"
    generation_warnings: list[str] = field(default_factory=list)
    generated_at: str = ""