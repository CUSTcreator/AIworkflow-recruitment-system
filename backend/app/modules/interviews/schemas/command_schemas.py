from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.app.modules.interviews.schemas.first_interview_plan_schemas import FirstInterviewPlanInput


class WorkflowRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generation_config: dict[str, Any] | None = None
    scoring_config: dict[str, Any] | None = None
    llm_config: dict[str, Any] | None = None


class GuideConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 保存/确认一面题单时必须通过 FirstInterviewPlanInput 的字段级校验。
    plan: FirstInterviewPlanInput | None = None
    generation_config: dict[str, Any] | None = None


class InterviewPlanDraftResponse(BaseModel):
    draft_version: int
    plan_version_id: str | None = None
    updated_at: str


class InterviewProgressDraftResponse(BaseModel):
    draft_version: int
    updated_at: str


class QuestionResponseWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    questionResponseId: str | None = Field(default=None, max_length=96)
    applicationId: str | None = Field(default=None, max_length=64)
    interviewId: str | None = Field(default=None, max_length=64)
    interviewRound: Literal["first", "second"] | None = None
    questionId: str = Field(min_length=1, max_length=96)
    questionText: str | None = Field(default=None, max_length=10_000)
    answerSummary: str | None = Field(default=None, max_length=50_000)
    rawText: str | None = Field(default=None, max_length=50_000)
    answerText: str | None = Field(default=None, max_length=50_000)
    answerStatus: Literal["answered", "not_recorded", "not_asked"] | None = None
    interviewerNote: str | None = Field(default=None, max_length=50_000)
    confirmedByInterviewer: bool | None = None


class InterviewWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["pass", "reject"] | None = None
    decisionReason: str | None = Field(default=None, max_length=5_000)
    progressDraftId: str | None = Field(default=None, max_length=96)
    applicationId: str | None = Field(default=None, max_length=64)
    rawNotes: str | None = Field(default=None, max_length=200_000)
    questionResponses: list[QuestionResponseWriteRequest] | None = Field(default=None, max_length=200)
    assessmentSummary: str | None = Field(default=None, max_length=5_000)
    overallRecommendation: str | None = Field(default=None, max_length=1_000)
    strengths: list[str] | None = Field(default=None, max_length=20)
    concerns: list[str] | None = Field(default=None, max_length=20)
    technicalFollowupNeeded: bool | None = None
    technicalFollowupItems: list[str] | None = Field(default=None, max_length=100)
    aiDraftItems: list[dict[str, Any]] | None = Field(default=None, max_length=200)
    items: list[dict[str, Any]] | None = Field(default=None, max_length=200)
    guideId: str | None = Field(default=None, max_length=96)
    createdAt: str | None = Field(default=None, max_length=64)
    updatedAt: str | None = Field(default=None, max_length=64)
    verificationTargetStatuses: dict[str, Literal["unverified", "partially_verified", "verified"]] | None = None
    scoring_config: dict[str, Any] | None = None


class InterviewCompleteRequest(InterviewWriteRequest):
    effectiveAt: datetime
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=64)

