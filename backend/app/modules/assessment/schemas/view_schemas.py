from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.app.modules.applications.public import (
    ApplicationView,
    CandidateView,
    JobView,
)
from backend.app.shared.recovery_actions import RecoveryActionView


class EvidenceDetailView(BaseModel):
    evidenceId: str
    rawText: str | None = None
    sourceLineStart: int | None = None
    sourceLineEnd: int | None = None
    sourceBulletId: str | None = None


class ScoringStatusView(BaseModel):
    applicationId: str
    jobId: str | None = None
    resumeSubmissionId: str | None = None
    workflowRunId: str | None = None
    workflowType: str | None = None
    runStatus: str
    applicationStatus: str
    stage: str
    updatedAt: str
    error: str | None = None
    process: dict[str, Any] | None = None
    recoveryActions: list[RecoveryActionView] = Field(default_factory=list)

class ScreeningSummaryReadModel(BaseModel):
    overallScore: float | None = None
    jobCapabilityFitScore: float | None = None
    resumeExperienceScore: float | None = None
    educationBackgroundScore: float | None = None
    qualificationStatus: str = "unclear"
    scoreExplanation: dict[str, Any] | None = None


class ScreeningEvidenceReadModel(BaseModel):
    evidenceId: str
    rawText: str = ""
    sourceLineStart: int | None = None
    sourceLineEnd: int | None = None
    sourceBulletId: str | None = None


class JobEvidenceReadModel(BaseModel):
    evidenceId: str
    sourceEvidenceIds: list[str] = Field(default_factory=list)
    evidenceType: str
    projectId: str | None = None
    score: float | None = None
    contentFitDescription: str
    qualityScore: float | None = None
    reason: str = ""


class JobCapabilityReadModel(BaseModel):
    capabilityId: str
    name: str
    definition: str
    role: Literal["core", "supporting"]
    score: float | None = None
    contentFitDescription: str
    evidenceQualityDescription: str
    evidence: list[JobEvidenceReadModel] = Field(default_factory=list)


class JobRequirementReadModel(BaseModel):
    jobUnitId: str
    sourceText: str
    sourceSection: str
    score: float | None = None
    coreCapabilities: list[JobCapabilityReadModel] = Field(default_factory=list)
    supportingCapabilities: list[JobCapabilityReadModel] = Field(default_factory=list)


class ResumeIndicatorReadModel(BaseModel):
    indicatorId: str
    name: str
    score: float | None = None
    levelDescription: str
    primaryProjectId: str | None = None
    primaryProjectName: str | None = None
    supportProjectIds: list[str] = Field(default_factory=list)
    evidenceIds: list[str] = Field(default_factory=list)


class ResumeCapabilityFrameworkReadModel(BaseModel):
    frameworkId: str
    frameworkName: str
    score: float | None = None
    activatedIndicatorCount: int = 0
    indicatorCount: int = 0
    indicators: list[ResumeIndicatorReadModel] = Field(default_factory=list)


class InterviewFocusReadModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    title: str
    priority: str | None = None
    severity: str | None = None
    reason: str | None = None
    expectedEvidence: str | None = None
    related_evidence_ids: list[str] = Field(default_factory=list)
    evidenceIds: list[str] = Field(default_factory=list)


class InterviewFocusCollectionReadModel(BaseModel):
    firstInterviewFocus: list[InterviewFocusReadModel] = Field(default_factory=list)

class ScreeningResultReadModel(BaseModel):
    applicationId: str
    summary: ScreeningSummaryReadModel
    jobRequirements: list[JobRequirementReadModel] = Field(default_factory=list)
    resumeCapabilities: list[ResumeCapabilityFrameworkReadModel] = Field(default_factory=list)
    interviewFocus: InterviewFocusCollectionReadModel
    evidenceIndex: dict[str, ScreeningEvidenceReadModel] = Field(default_factory=dict)
    viewSchemaVersion: Literal["screening_result_view_v2_0"]


class HardScreeningRequirementView(BaseModel):
    """One frozen hard-screening rule paired with its evaluation result."""

    model_config = ConfigDict(extra="forbid")

    ruleId: str
    name: str
    requirementText: str
    status: Literal["passed", "failed", "manual_review", "not_evaluated"]
    reason: str = ""
    reasonCode: str | None = None
    sourceQuotes: list[str] = Field(default_factory=list)


class HardScreeningCountsView(BaseModel):
    """Backend-computed item counts for the compact hard-screening card."""

    model_config = ConfigDict(extra="forbid")

    total: int = Field(default=0, ge=0)
    passed: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    manualReview: int = Field(default=0, ge=0)
    notEvaluated: int = Field(default=0, ge=0)


class HardScreeningReviewView(BaseModel):
    """Frozen hard-screening snapshot used by this V1 assessment."""

    model_config = ConfigDict(extra="forbid")

    resultId: str | None = None
    policyId: str | None = None
    status: Literal[
        "not_configured", "pending", "running", "passed", "failed", "manual_review"
    ] = "not_configured"
    summary: str = ""
    # Counts are part of the server-owned display contract. The browser only renders them.
    counts: HardScreeningCountsView = Field(default_factory=HardScreeningCountsView)
    requirements: list[HardScreeningRequirementView] = Field(default_factory=list)


class DecisionFocusItemView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    focusId: str
    title: str
    priority: Literal["high", "medium", "low"]
    oneLineReason: str = ""
    currentConclusion: str = ""
    verificationAction: str = ""
    status: str
    sourceRefs: list[str] = Field(default_factory=list)
    missingInformation: str = ""


class DecisionFocusCollectionView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[DecisionFocusItemView] = Field(default_factory=list)


class AssessmentCoverageView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    planned: int = 0
    fullyAssessed: int = 0
    partiallyAssessed: int = 0
    notAssessed: int = 0
    coverageRate: float = 0.0


class StageHandoffView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recommendation: str = ""
    decisionReason: str = ""
    confirmedStrengths: list[str] = Field(default_factory=list)
    remainingItems: list[str] = Field(default_factory=list)


class HrConditionView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conditionId: str
    label: str
    status: Literal["positive", "neutral", "concern"]
    value: str


class DecisionSupportView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["decision_support_v2_0"]
    decisionFocus: DecisionFocusCollectionView
    assessmentCoverage: AssessmentCoverageView
    stageHandoff: StageHandoffView | None = None
    hrConditions: list[HrConditionView] = Field(default_factory=list)

class DecisionAssessmentView(BaseModel):
    totalScore: float | None = None
    jobFitScore: float | None = None
    experienceScore: float | None = None
    educationScore: float | None = None
    qualificationStatus: str = "unclear"


class DecisionSummaryItemView(BaseModel):
    title: str
    summary: str
    sourceSignalIds: list[str] = Field(default_factory=list)
    evidenceIds: list[str] = Field(default_factory=list)


class AiSummaryView(BaseModel):
    recommendationLevel: Literal[
        "strongly_recommend", "recommend", "cautious_recommend", "not_recommend", "strongly_not_recommend"
    ]
    recommendationReason: str = ""
    strengths: list[DecisionSummaryItemView] = Field(default_factory=list)
    weaknesses: list[DecisionSummaryItemView] = Field(default_factory=list)
    generationMode: Literal["llm", "rule_fallback"]


class VerificationFocusItemView(BaseModel):
    focusId: str
    focusType: str
    title: str
    reason: str = ""
    verificationGoal: str = ""
    sourceInterviewTargetIds: list[str] = Field(default_factory=list)
    status: str = "open"
    evidenceIds: list[str] = Field(default_factory=list)


class VerificationFocusView(BaseModel):
    items: list[VerificationFocusItemView] = Field(default_factory=list)
    generationMode: Literal["llm", "rule_fallback"]


class CandidateDecisionOverviewView(BaseModel):
    # 已发布的实际 AAV 阶段，前端卡片标题不得依据页面路由或 Application 状态猜测。
    assessmentStage: Literal["screening", "after_first_interview", "after_second_interview"] = "screening"
    assessment: DecisionAssessmentView
    aiSummary: AiSummaryView
    verificationFocus: VerificationFocusView
    sourceSnapshotHash: str = ""
    generatedAt: str


class ScreeningReviewView(BaseModel):
    application: ApplicationView
    candidate: CandidateView
    job: JobView
    hardScreening: HardScreeningReviewView
    screeningResult: ScreeningResultReadModel
    decisionOverview: CandidateDecisionOverviewView
    decisionSupport: DecisionSupportView
    evidenceIndex: dict[str, ScreeningEvidenceReadModel] = Field(default_factory=dict)
    availableActions: list[str] = Field(default_factory=list)
    recoveryActions: list[RecoveryActionView] = Field(default_factory=list)
    workflowStatus: ScoringStatusView
    viewSchemaVersion: Literal["screening_review_v2"] = "screening_review_v2"
