from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from backend.app.modules.applications.public import (
    ApplicationView,
    CandidateView,
    JobView,
)
from backend.app.modules.assessment.public import (
    CandidateDecisionOverviewView,
    DecisionSupportView,
    HardScreeningReviewView,
)
from backend.app.shared.recovery_actions import RecoveryActionView


class FirstInterviewWorkspaceView(BaseModel):
    application: ApplicationView
    candidate: CandidateView
    job: JobView
    hardScreening: HardScreeningReviewView
    screening: dict[str, Any]
    plan: dict[str, Any] | None = None
    planningState: dict[str, Any] = Field(default_factory=dict)
    progressDraft: dict[str, Any] | None = None
    decisionSummary: dict[str, Any] | None = None
    decisionOverview: CandidateDecisionOverviewView
    decisionSupport: DecisionSupportView
    evidenceIndex: dict[str, Any] = Field(default_factory=dict)
    availableActions: list[str] = Field(default_factory=list)
    recoveryActions: list[RecoveryActionView] = Field(default_factory=list)
    workflowStatus: dict[str, Any] = Field(default_factory=dict)
    viewSchemaVersion: str = "first_interview_workspace_v2"
class FirstInterviewPlanView(FirstInterviewWorkspaceView):
    """Read model dedicated to the first-interview planning page."""

    viewSchemaVersion: str = "first_interview_plan_v3"


class FirstInterviewEvaluationView(FirstInterviewWorkspaceView):
    """Read model dedicated to the first-interview evaluation page."""

    viewSchemaVersion: str = "first_interview_evaluation_v2"


class InterviewRawNoteView(BaseModel):
    content: str = ""
    authorName: str = ""
    createdAt: str = ""


class RecordedQuestionView(BaseModel):
    questionId: str
    questionText: str = ""
    answerSummary: str = ""
    interviewerNote: str = ""


class FirstInterviewOriginalRecordView(BaseModel):
    rawNotes: InterviewRawNoteView | None = None
    recordedQuestions: list[RecordedQuestionView] = Field(default_factory=list)


class SecondInterviewOriginalRecordView(BaseModel):
    rawNotes: InterviewRawNoteView | None = None


class SecondInterviewWorkspaceView(BaseModel):
    application: ApplicationView
    candidate: CandidateView
    job: JobView
    hardScreening: HardScreeningReviewView
    screening: dict[str, Any]
    plan: dict[str, Any] | None = None
    progressDraft: dict[str, Any] | None = None
    reviewPackage: dict[str, Any] | None = None
    finalPackage: dict[str, Any] | None = None
    hrAssessment: dict[str, Any] | None = None
    firstAssessment: dict[str, Any] | None = None
    firstEvidence: list[dict[str, Any]] = Field(default_factory=list)
    firstOriginalRecord: FirstInterviewOriginalRecordView | None = None
    secondOriginalRecord: SecondInterviewOriginalRecordView | None = None
    screeningScoreSnapshot: dict[str, Any] | None = None
    afterFirstScoreSnapshot: dict[str, Any] | None = None
    afterSecondScoreSnapshot: dict[str, Any] | None = None
    # V2/V3 已发布 AAV 相对上一版的变化；三个正式页面共用该 DTO，
    # 不允许由前端读取 JSON 后再自行计算。
    afterFirstAssessmentChanges: dict[str, Any] | None = None
    afterSecondAssessmentChanges: dict[str, Any] | None = None
    currentScoreSnapshot: dict[str, Any] | None = None
    decisionSummary: dict[str, Any] | None = None
    decisionSupport: DecisionSupportView
    decisionOverview: CandidateDecisionOverviewView
    nonCapabilityCard: dict[str, Any] = Field(default_factory=dict)

    evidenceIndex: dict[str, Any] = Field(default_factory=dict)
    availableActions: list[str] = Field(default_factory=list)
    recoveryActions: list[RecoveryActionView] = Field(default_factory=list)
    workflowStatus: dict[str, Any] = Field(default_factory=dict)
    viewSchemaVersion: str = "second_interview_workspace_v2"


class SecondInterviewReviewView(SecondInterviewWorkspaceView):
    """Read model dedicated to the HR second-interview review page."""

    viewSchemaVersion: str = "second_interview_review_v2"


class FinalReviewView(SecondInterviewWorkspaceView):
    """Read model dedicated to the final decision page."""

    viewSchemaVersion: str = "final_review_v2"
