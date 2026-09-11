from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field
from backend.app.shared.recovery_actions import RecoveryActionView


class HardScreeningReasonPreview(BaseModel):
    ruleName: str
    reason: str


class HardScreeningPreview(BaseModel):
    totalCount: int = 0
    passedCount: int = 0
    failedCount: int = 0
    reviewCount: int = 0
    reasons: list[HardScreeningReasonPreview] = Field(default_factory=list)


class ApplicationWorkflowProcessView(BaseModel):
    """招聘流程列表中的辅助 Workflow 状态。"""

    workflowRunId: str
    workflowType: str
    processStatus: str
    currentStep: str = ""
    currentStepLabel: str = ""
    attemptCount: int = 0
    maxAttempts: int = 0
    pollCount: int = 0
    maxPollAttempts: int = 0
    nextAttemptAt: str = ""
    publicMessage: str = ""
    recoveryAction: str = ""
    recoveryActionLabel: str = ""
    updatedAt: str = ""


class ApplicationPreScreeningProcessView(BaseModel):
    """招聘流程页从 Application 创建到 V1 完成前的只读聚合状态。"""

    status: Literal[
        "waiting_job_profile",
        "job_profile_queued",
        "job_profile_processing",
        "job_profile_review_required",
        "job_profile_failed",
        "initial_assessment_failed",
        "hard_screening_queued",
        "hard_screening_running",
        "hard_screening_failed",
        "hard_screening_review_required",
        "v1_scheduling",
        "v1_queued",
        "v1_running",
        "v1_failed",
    ]
    label: str
    message: str = ""
    tone: Literal["neutral", "info", "warning", "danger"] = "neutral"
    workflowRunId: str | None = None


class AssessmentStageViewAction(BaseModel):
    """已发布评估结果的只读入口；它不是异常恢复动作。"""

    action: str
    label: str
    route: str


class ApplicationWorkspaceAction(BaseModel):
    """后端授权并投影的工作台导航入口。"""

    action: str
    label: str
    route: str


class AssessmentStageSummaryView(BaseModel):
    """候选人列表中每个 V1/V2/V3 结果的最小业务摘要。

    ``resultAvailable`` 只由正式评估版本的 published_at 决定；运行中的任务
    通过 status 单独表达，不能用列表状态猜测是否存在可查看结果。
    """

    stage: Literal["v1", "v2", "v3"]
    label: str
    status: Literal["not_started", "processing", "recoverable", "completed"] = "not_started"
    resultAvailable: bool = False
    publishedAssessmentVersionId: str | None = None
    viewAction: AssessmentStageViewAction | None = None


class ApplicationCurrentExecutionView(BaseModel):
    """当前仍需关注的一个执行任务；已完成历史任务不会进入这里。"""

    stage: str
    label: str
    process: ApplicationWorkflowProcessView


class ApplicationListItem(BaseModel):
    applicationId: str
    jobId: str
    candidateName: str
    anonymizedCode: str = ""
    currentTitle: str = ""
    age: int | None = None
    yearsOfExperience: str = ""
    school: str = ""
    major: str = ""
    highestDegree: str = ""
    resumePdfUrl: str | None = None
    resumeSubmissionId: str | None = None
    resumeFilename: str | None = None
    documentStatus: str = "completed"
    candidateResolved: bool = True
    processingStage: str = ""
    processingError: str = ""
    canRetry: bool = False
    jobTitle: str
    jobMajorRequirement: str = ""
    department: str = ""
    jobConfigurationStatus: Literal["complete", "incomplete"] = "complete"
    missingJobAssignments: list[
        Literal["hiring_manager", "department_recruiter"]
    ] = Field(default_factory=list)
    canConfigureJob: bool = False
    status: str
    resumeRebuildStatus: Literal["idle", "processing", "review_required", "completed", "failed"] = "idle"
    resumeRebuildMessage: str = ""
    resumeRebuildProcess: ApplicationWorkflowProcessView | None = None
    # 页面状态只读聚合既有事实，绝不能反向成为第二套 Application 状态机。
    preScreeningProcess: ApplicationPreScreeningProcessView | None = None
    mainRoute: str
    workspaceAction: ApplicationWorkspaceAction | None = None
    rejectionStage: Literal[
        "hard_screening",
        "screening",
        "first_interview",
        "hr_review",
        "second_interview",
        "final_review",
    ] | None = None
    hardScreeningStatus: Literal[
        "not_configured",
        "pending",
        "running",
        "passed",
        "failed",
        "manual_review",
    ] = "not_configured"
    hardScreeningSummary: str = ""
    hardScreeningPreview: HardScreeningPreview = Field(default_factory=HardScreeningPreview)
    hardScreeningProcess: ApplicationWorkflowProcessView | None = None
    dueAt: str = ""
    overdue: bool = False
    scoreStatus: Literal["scored", "failed", "pending"] = "pending"
    screeningError: str = ""
    # 初筛 V1 属于 Application 级任务；用于在招聘流程页打开本申请的真实执行轨迹。
    screeningProcess: ApplicationWorkflowProcessView | None = None
    baseScore: float | None = None
    currentScore: float | None = None
    scoreStage: str | None = None
    assessmentUpdateStatus: Literal["idle", "queued", "running", "review_required", "failed", "completed"] = "idle"
    assessmentUpdateStage: Literal["first", "second"] | None = None
    assessmentUpdateMessage: str = ""
    assessmentUpdateProcess: ApplicationWorkflowProcessView | None = None
    # V2/V3 可随时重新计算；运行中的旧任务会先由后端安全替代。
    canRetryAssessmentUpdate: bool = False
    # 所有可执行按钮均由后端按用户、部门范围和申请状态计算；前端不自行推断权限。
    availableActions: list[str] = Field(default_factory=list)
    # 异常恢复动作是结构化合同；普通阶段操作仍保留在 availableActions。
    recoveryActions: list[RecoveryActionView] = Field(default_factory=list)
    # 结果入口和执行轨迹分离：前者可在后续阶段继续查看，后者只保留当前未完成任务。
    assessmentStages: list[AssessmentStageSummaryView] = Field(default_factory=list)
    currentExecution: ApplicationCurrentExecutionView | None = None
    interviewReviewRequired: bool = False
    interviewReviewCount: int = 0
    qualificationGate: str = "unclear"
    submittedAt: str = ""
    updatedAt: str = ""


class ApplicationListView(BaseModel):
    items: list[ApplicationListItem]
    page: int
    pageSize: int
    total: int
    groupCounts: dict[str, int] = Field(default_factory=dict)


class ApplicationFilterJobOption(BaseModel):
    jobId: str
    title: str
    departmentId: str
    departmentName: str


class ApplicationFilterDepartmentOption(BaseModel):
    departmentId: str
    name: str


class ApplicationFilterOptionsView(BaseModel):
    departments: list[ApplicationFilterDepartmentOption] = Field(default_factory=list)
    jobs: list[ApplicationFilterJobOption] = Field(default_factory=list)


