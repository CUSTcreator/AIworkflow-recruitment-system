"""一面题单保存与确认请求的结构化契约。

本模块是浏览器写入后端的唯一字段边界：页面展示 DTO 可以附带版本和提示信息，但保存时必须
符合这里的题单、Target、问题和 Rubric 形状。规则层的 snake_case 内部对象不允许直接作为 HTTP
请求传入。
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RubricLevelInput(BaseModel):
    """一个评分等级锚点。"""

    model_config = ConfigDict(extra="forbid")

    level: int = Field(ge=1, le=5)
    description: str = Field(min_length=1, max_length=2_000)


class EvaluationRubricInput(BaseModel):
    """现场表现题使用的单个能力评分 Rubric。"""

    model_config = ConfigDict(extra="forbid")

    rubricId: str = Field(min_length=1, max_length=128)
    targetType: Literal["job_capability", "preset_indicator"]
    targetId: str = Field(min_length=1, max_length=128)
    maxLevel: int = Field(ge=1, le=5)
    levelAnchors: list[RubricLevelInput] = Field(default_factory=list, max_length=5)


class VerificationTargetInput(BaseModel):
    """题单中展示、且可被问题绑定的冻结 InterviewTarget 投影。"""

    model_config = ConfigDict(extra="forbid")

    targetId: str = Field(min_length=1, max_length=128)
    round: Literal["first"] = "first"
    requirementId: str = Field(default="", max_length=128)
    title: str = Field(default="", max_length=300)
    unknownPoint: str = Field(default="", max_length=5_000)
    sourceEvidenceIds: list[str] = Field(default_factory=list, max_length=100)
    priority: Literal["high", "medium", "low"] = "medium"
    selected: bool = True
    # 以下字段并非当前页面的主要展示内容，但会进入一面后解析和 V2 的事实链路。
    purpose: Literal["verify_experience", "elicit_missing", "direct_demonstration"] | None = None
    targetType: Literal["job_capability", "preset_indicator"] | None = None
    triggerCode: str | None = Field(default=None, max_length=96)


class InterviewQuestionInput(BaseModel):
    """一面正式题或 AI 建议题的写入形状。

    文本类字段允许面试官编辑；Target、证据、题型、场景与 Rubric 是规则层冻结字段，前端保存时
    必须原样传回。自定义题可不绑定 Target，并默认 ``resultType=non_scoring``。
    """

    model_config = ConfigDict(extra="forbid")

    questionId: str = Field(min_length=1, max_length=128)
    verificationTargetIds: list[str] = Field(default_factory=list, max_length=3)
    mainQuestion: str = Field(default="", max_length=10_000)
    followUpQuestions: list[str] = Field(default_factory=list, max_length=3)
    expectedEvidence: list[str] = Field(default_factory=list, max_length=5)
    negativeSignals: list[str] = Field(default_factory=list, max_length=5)
    priority: Literal["high", "medium", "low"] = "medium"
    confirmed: bool = False
    sourceType: Literal["ai_suggestion", "interviewer_custom", "common_template"] | None = None
    sectionType: Literal["technical", "common"] | None = None
    resultType: Literal["capability", "non_scoring"] | None = None
    evaluationPoints: list[str] | None = Field(default=None, max_length=20)
    required: bool | None = None
    templateVersionId: str | None = Field(default=None, max_length=96)
    sourceSuggestionId: str | None = Field(default=None, max_length=128)
    finalText: str | None = Field(default=None, max_length=10_000)
    isEdited: bool | None = None
    finalQuestionId: str | None = Field(default=None, max_length=128)
    recommendation: Literal["high", "medium", "low"] | None = None
    validationGoal: str | None = Field(default=None, max_length=5_000)
    slotId: str | None = Field(default=None, max_length=128)
    probeAngle: Literal[
        "fact_reconstruction", "contribution_boundary", "evidence_crosscheck", "capability_probe", "direct_task"
    ] | None = None
    questionType: Literal["experience_probe", "capability_probe", "direct_task"] | None = None
    purpose: Literal["verify_experience", "elicit_missing", "direct_demonstration"] | None = None
    expectedResultType: Literal["experience_fact", "interview_performance"] | None = None
    scenarioId: str | None = Field(default=None, max_length=128)
    targetJobCapabilityIds: list[str] = Field(default_factory=list, max_length=3)
    targetPresetIndicatorIds: list[str] = Field(default_factory=list, max_length=3)
    sourceEvidenceIds: list[str] = Field(default_factory=list, max_length=100)
    evaluationRubrics: list[EvaluationRubricInput] = Field(default_factory=list, max_length=3)


class InterviewPlanSectionInput(BaseModel):
    """确认后题单的展示分区；分区不参与题目评分规则计算。"""

    model_config = ConfigDict(extra="forbid")

    sectionType: Literal["technical", "common"]
    title: str = Field(min_length=1, max_length=300)
    questions: list[InterviewQuestionInput] = Field(default_factory=list, max_length=20)


class FirstInterviewPlanInput(BaseModel):
    """一面题单草稿保存与确认请求。

    新题单可以没有 Open Target 或技术题；此时仍允许保存和确认，由通用模板决定是否附加通用问题。
    重跑题单时，页面只会提交最新 PlanVersion 的草稿。
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    # guide_version 是旧草稿字段；新 DTO 统一使用 guideVersion，读取历史草稿时两者都接受。
    guideVersion: str | None = Field(default=None, max_length=96)
    guide_version: str | None = Field(default=None, max_length=96)
    planId: str = Field(min_length=1, max_length=128)
    applicationId: str = Field(min_length=1, max_length=64)
    planVersionId: str | None = Field(default=None, max_length=96)
    planVersion: int | None = Field(default=None, ge=1)
    planStatus: Literal["draft", "superseded", "confirmed"] | None = None
    sourceAssessmentVersionId: str | None = Field(default=None, max_length=96)
    round: Literal["first"] = "first"
    guideType: Literal["technical_first_round"] = "technical_first_round"
    title: str = Field(default="技术一面题单草稿", max_length=300)
    summary: str = Field(default="", max_length=5_000)
    planningSummary: str | None = Field(default=None, max_length=5_000)
    goal: str = Field(min_length=1, max_length=5_000)
    durationMinutes: int = Field(ge=1, le=240)
    questionCount: int = Field(ge=0, le=20)
    targets: list[VerificationTargetInput] = Field(default_factory=list, max_length=5)
    questions: list[InterviewQuestionInput] = Field(default_factory=list, max_length=20)
    technicalQuestions: list[InterviewQuestionInput] | None = Field(default=None, max_length=8)
    commonQuestions: list[InterviewQuestionInput] | None = Field(default=None, max_length=20)
    questionSuggestions: list[InterviewQuestionInput] | None = Field(default=None, max_length=8)
    recommendedQuestionIds: list[str] | None = Field(default=None, max_length=8)
    coverage: dict[str, Any] | None = None
    generationNotes: list[str] | None = Field(default=None, max_length=20)
    generationMode: Literal["llm", "rule_fallback"] | None = None
    generationWarnings: list[str] | None = Field(default=None, max_length=50)
    generatedAt: str | None = Field(default=None, max_length=64)
    interviewerNotes: str | None = Field(default=None, max_length=5_000)
    confirmed: bool = False
    guideStatus: Literal["draft", "confirmed"] | None = None
    draftRevision: int | None = Field(default=None, ge=0)
    updatedAt: str | None = Field(default=None, max_length=64)
    updatedBy: str | None = Field(default=None, max_length=64)
    commonTemplate: dict[str, Any] | None = None
    commonTemplateStatus: Literal["ready", "missing"] | None = None
    commonTemplateVersion: int | None = Field(default=None, ge=1)
    commonTemplateVersionId: str | None = Field(default=None, max_length=96)
    sections: list[InterviewPlanSectionInput] | None = Field(default=None, max_length=2)

    @model_validator(mode="after")
    def validate_question_ids(self) -> "FirstInterviewPlanInput":
        """防止保存载荷中的题目或目标标识重复，空题单仍然允许。"""
        question_ids = [item.questionId for item in self.questions]
        target_ids = [item.targetId for item in self.targets]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("first_interview_question_ids_must_be_unique")
        if len(target_ids) != len(set(target_ids)):
            raise ValueError("first_interview_target_ids_must_be_unique")
        return self