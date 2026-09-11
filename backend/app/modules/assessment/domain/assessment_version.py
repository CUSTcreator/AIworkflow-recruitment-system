"""V1/V2/V3 评估版本领域模型。

本文件是后端评估版本数据的唯一类型来源：
- 持久化 JSON 使用 snake_case；
- HTTP ReadModel 由 schemas/view_schemas.py 转换为 camelCase；
- Workflow、Service、Publisher 和 ReadModel 均导入这些模型，不再自行定义匿名字典字段。
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AssessmentStage(str, Enum):
    """业务评估阶段；V1/V2/V3 由阶段而非数据库 version 数值区分。"""

    SCREENING = "screening"
    AFTER_FIRST_INTERVIEW = "after_first_interview"
    AFTER_SECOND_INTERVIEW = "after_second_interview"


class InterviewTargetStatus(str, Enum):
    """跨阶段核验目标的唯一持久化状态。"""

    OPEN = "open"
    RESOLVED = "resolved"


class SignalChangeStatus(str, Enum):
    """相对前一正式评估版本的展示变化，不是工作流状态。"""

    ADDED = "added"
    RETAINED = "retained"
    CLOSED = "closed"


class AssessmentSourceManifest(BaseModel):
    """AAV.source_json 的合同；仅记录来源身份与版本，不复制来源内容。

    ``prior_interview_parse_result_ids`` 是已发布 IPR 的显式链：V2 为空，V3 至少
    包含一面 IPR。当前轮 IPR 在发布事务中生成后写回 ``current_interview_parse_result_id``，
    从而可以只依赖 AAV.source_json 完整重放和审计来源。
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["assessment_source_v1", "assessment_source_v2"] = "assessment_source_v2"
    previous_assessment_version_id: str = Field(description="本轮直接读取的已发布前一 AAV。")
    prior_interview_parse_result_ids: list[str] = Field(default_factory=list, description="前序已发布 IPR 的时间顺序链。")
    current_interview_parse_result_id: str | None = Field(default=None, description="本轮发布后新建 IPR 的 ID；冻结时为空。")
    interview_record_ids: list[str] = Field(default_factory=list, description="本次解析使用的不可变面试记录 ID。")
    first_interview_plan_version_id: str | None = Field(default=None, description="仅 V2 可填，用于解析一面题目与核验目标映射。")
    first_interview_guide_hash: str | None = Field(default=None, description="仅 V2 可填，确认后正式题单与逐题记录的冻结内容哈希。")
    algorithm_versions: dict[str, str] = Field(default_factory=dict, description="评分算法及数据集版本。")
    rule_version: str = Field(default="", description="确定性规则版本。")
    prompt_version: str = Field(default="", description="LLM 展示提示词版本；不参与评分。")
    source_input_hash: str = Field(description="冻结来源 ID、版本和内容哈希组成的可复现摘要。")
    # 同一 Workflow 发布重试必须复用；用户新发起的重算则拥有新的业务请求身份。
    publication_key: str | None = Field(default=None, description="面后正式发布的稳定业务幂等键。")


class ScoreChange(BaseModel):
    """同一评分维度相对前一版的原始数值变化。"""

    model_config = ConfigDict(extra="forbid")

    metric: Literal["total", "job_fit", "experience", "education"]
    previous_value: float | None = None
    current_value: float | None = None
    delta: float | None = None


class CapabilityChange(BaseModel):
    """一个受面试观察影响的能力结果变化；文案由展示层另行生成。"""

    model_config = ConfigDict(extra="forbid")

    result_ref: str
    capability_id: str
    previous_score: float | None = None
    current_score: float | None = None
    delta: float | None = None
    reason_code: str = ""
    evidence_ids: list[str] = Field(default_factory=list)


class AssessmentScoreResult(BaseModel):
    """AAV.core_result_json.score_result 的统一分数结构。"""

    model_config = ConfigDict(extra="allow")

    total: float | None = None
    job_fit: float | None = None
    experience: float | None = None
    education: float | None = None
    qualification_status: str = "unclear"


class AssessmentCoreResult(BaseModel):
    """AAV.core_result_json 的合同；由纯算法产生，不能包含 LLM 展示字段。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["assessment_core_result_v1"] = "assessment_core_result_v1"
    experience_result: dict[str, Any] = Field(default_factory=dict)
    job_result: dict[str, Any] = Field(default_factory=dict)
    education_result: dict[str, Any] = Field(default_factory=dict)
    score_result: AssessmentScoreResult
    capability_graph: dict[str, Any] = Field(default_factory=dict)
    affected_result_refs: list[str] = Field(default_factory=list)
    score_changes: list[ScoreChange] = Field(default_factory=list)
    capability_changes: list[CapabilityChange] = Field(default_factory=list)


class AssessmentSignal(BaseModel):
    """规则产生的优势或薄弱项；signal_key 必须跨版本稳定，不能使用排名序号。"""

    model_config = ConfigDict(extra="forbid")

    signal_key: str
    source_type: str
    target_id: str
    title: str = ""
    reason: str = ""
    source_result_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    current_rank: int | None = None
    previous_rank: int | None = None


class SignalComparisonItem(AssessmentSignal):
    """V2/V3 前后 Signal 并集中的一个条目。"""

    change_status: SignalChangeStatus


class InterviewTargetUpdate(BaseModel):
    """准备写入 InterviewTarget 表的状态与溯源字段。"""

    model_config = ConfigDict(extra="forbid")

    interview_target_id: str
    purpose: str
    target_type: str
    target_id: str
    title: str = ""
    verification_goal: str = ""
    trigger_code: str = ""
    status: InterviewTargetStatus
    stage_created: AssessmentStage
    resolved_stage: AssessmentStage | None = None
    resolution_note: str | None = None
    source_result_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class SignalComparison(BaseModel):
    """前后两版 Top 4 Signal 的并集；每一类最多八项。"""

    model_config = ConfigDict(extra="forbid")

    baseline_assessment_version_id: str
    strengths: list[SignalComparisonItem] = Field(default_factory=list, max_length=8)
    weaknesses: list[SignalComparisonItem] = Field(default_factory=list, max_length=8)


class TargetComparison(BaseModel):
    """Target 只存在 open/resolved 两种业务状态，新增 Target 初始即为 open。"""

    model_config = ConfigDict(extra="forbid")

    baseline_assessment_version_id: str
    opened_target_ids: list[str] = Field(default_factory=list)
    resolved_target_ids: list[str] = Field(default_factory=list)
    remaining_open_target_ids: list[str] = Field(default_factory=list)


class AssessmentRuleResult(BaseModel):
    """AAV.rule_result_json 的合同；优势、薄弱项是当前 Top 4，比较项另存并集。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["assessment_rule_result_v1"] = "assessment_rule_result_v1"
    strength_signals: list[AssessmentSignal] = Field(default_factory=list, max_length=4)
    weakness_signals: list[AssessmentSignal] = Field(default_factory=list, max_length=4)
    target_updates: list[InterviewTargetUpdate] = Field(default_factory=list)
    signal_comparison: SignalComparison | None = None
    target_comparison: TargetComparison | None = None


class PresentationSignal(BaseModel):
    """展示层的 Signal 文案；只能补 title/summary，不能重写规则 ID 或证据。"""

    model_config = ConfigDict(extra="forbid")

    signal_key: str
    title: str
    summary: str
    evidence_ids: list[str] = Field(default_factory=list)


class VerificationFocus(BaseModel):
    """展示给招聘人员的核验事项，状态直接映射 Target 的 open/resolved。"""

    model_config = ConfigDict(extra="forbid")

    target_id: str
    status: InterviewTargetStatus
    title: str
    reason: str = ""
    goal: str = ""
    evidence_ids: list[str] = Field(default_factory=list)


class RoundChangeSummary(BaseModel):
    """V2/V3 的本轮变化摘要；V1 不生成此对象。"""

    model_config = ConfigDict(extra="forbid")

    title: str
    summary: str
    score_changes: list[ScoreChange] = Field(default_factory=list)
    capability_changes: list[CapabilityChange] = Field(default_factory=list)


class AssessmentPresentationResult(BaseModel):
    """AAV.presentation_json 的合同；LLM 只能产出该对象内的自然语言字段。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["assessment_presentation_result_v1"] = "assessment_presentation_result_v1"
    # 推荐程度由后端 recommendation_policy_v1 根据冻结评分和核心缺口生成；
    # LLM 只负责摘要文案，不得修改该等级，也不触发工作流状态转换。
    recommendation_level: Literal[
        "strongly_recommend", "recommend", "cautious_recommend", "not_recommend", "strongly_not_recommend"
    ] = "cautious_recommend"
    recommendation_reason: str = ""
    strengths: list[PresentationSignal] = Field(default_factory=list)
    weaknesses: list[PresentationSignal] = Field(default_factory=list)
    verification_focus: list[VerificationFocus] = Field(default_factory=list)
    round_change_summary: RoundChangeSummary | None = None
    generation_mode: Literal["llm", "rule_fallback"]
    # 单次 Bundle 调用统一生成；两个字段保留给现有前端合同，值始终一致。
    summary_generation_mode: Literal["llm", "rule_fallback"] = "rule_fallback"
    verification_focus_generation_mode: Literal["llm", "rule_fallback"] = "rule_fallback"
    source_input_hash: str






