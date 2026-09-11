"""一面题单规划的冻结输入契约。

本模块只定义跨边界的数据形状。ORM 查询、LLM 调用和版本发布均不得放在这里。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class FirstInterviewPlanningConfig:
    """题单数量与关联边界。

    ``max_open_interview_targets`` 固定为 V1 选择出的 5 个上限；``max_question_suggestions`` 是
    本轮所有候选题的总上限；``max_questions_per_target`` 和 ``max_targets_per_question`` 约束题目
    与核验目标的关联范围。Open InterviewTarget 的数量不允许在题单阶段被静默截断。
    """

    max_open_interview_targets: int = 5
    max_question_suggestions: int = 8
    max_questions_per_target: int = 3
    max_targets_per_question: int = 3

    def __post_init__(self) -> None:
        # Open Target 上限是 V1 正式选择规则，不允许调用方通过 generation_config 悄悄放宽。
        if self.max_open_interview_targets != 5:
            raise ValueError("max_open_interview_targets_must_be_5")
        if not 1 <= self.max_question_suggestions <= 8:
            raise ValueError("max_question_suggestions_out_of_range")
        if not 1 <= self.max_questions_per_target <= 3:
            raise ValueError("max_questions_per_target_out_of_range")
        if not 1 <= self.max_targets_per_question <= 3:
            raise ValueError("max_targets_per_question_out_of_range")


@dataclass(slots=True)
class FirstInterviewPlanningInput:
    """已冻结的一面题单输入。

    ``interview_targets`` 是当前 Application 的全部 Open Target 快照；每一项必须保留目标、
    来源结果与证据 ID。``job_profile``、``resume_profile`` 仅用于补全这些目标关联的定义和证据，
    不能据此凭空新增核验目标。

    字段分组：application/candidate/job 用于业务定位；resume_profile/job_profile 是冻结事实；
    evidence_records 是可引用原文；interview_targets 是核验边界；metadata 只保存来源版本、模板、
    策略与 LLM 配置等追溯信息。该对象创建后不应由下游步骤修改。
    """

    application_id: str
    candidate_id: str = ""
    job_id: str = ""
    job_title: str = ""
    job_profile: dict[str, Any] = field(default_factory=dict)
    resume_profile: dict[str, Any] = field(default_factory=dict)
    evidence_records: list[dict[str, Any]] = field(default_factory=list)
    interview_targets: list[dict[str, Any]] = field(default_factory=list)
    generation_config: FirstInterviewPlanningConfig = field(default_factory=FirstInterviewPlanningConfig)
    metadata: dict[str, Any] = field(default_factory=dict)