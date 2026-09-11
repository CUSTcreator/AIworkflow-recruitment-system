import type { Application, Candidate, Job } from "@/modules/applications/contracts";
import type { WorkflowProcess } from "@/shared/workflows/process";
import type { RecoveryAction } from "@/shared/recovery/actions";

export type GateStatus = "verified" | "unclear" | "not_qualified";

export type EvidenceStrength = "strong" | "moderate" | "weak";
export type CoverageStatus = "covered" | "partially_covered" | "not_covered" | "unclear";

export interface EvidenceIndexItem {
  evidenceId: string;
  rawText?: string;
  sourceLineStart?: number;
  sourceLineEnd?: number;
  sourceBulletId?: string;
}

export interface JobEvidenceView {
  evidenceId: string;
  sourceEvidenceIds: string[];
  evidenceType: string;
  projectId?: string;
  score: number | null;
  contentFitDescription: string;
  qualityScore: number | null;
  reason: string;
}

export interface JobCapabilityView {
  capabilityId: string;
  name: string;
  definition: string;
  role: "core" | "supporting";
  score: number | null;
  contentFitDescription: string;
  evidenceQualityDescription: string;
  evidence: JobEvidenceView[];
}

export interface JobRequirementView {
  jobUnitId: string;
  sourceText: string;
  sourceSection: string;
  score: number | null;
  coreCapabilities: JobCapabilityView[];
  supportingCapabilities: JobCapabilityView[];
}

export interface ResumeIndicatorView {
  indicatorId: string;
  name: string;
  score: number | null;
  levelDescription: string;
  primaryProjectId?: string;
  primaryProjectName?: string;
  supportProjectIds: string[];
  evidenceIds: string[];
}

export interface ResumeCapabilityFrameworkView {
  frameworkId: string;
  frameworkName: string;
  score: number | null;
  activatedIndicatorCount: number;
  indicatorCount: number;
  indicators: ResumeIndicatorView[];
}

export interface ScreeningResultV2 {
  applicationId: string;
  summary: {
    overallScore: number | null;
    jobCapabilityFitScore: number | null;
    resumeExperienceScore: number | null;
    educationBackgroundScore: number | null;
    qualificationStatus: GateStatus;
    scoreExplanation?: Record<string, unknown>;
  };
  jobRequirements: JobRequirementView[];
  resumeCapabilities: ResumeCapabilityFrameworkView[];
  interviewFocus: {
    firstInterviewFocus: Array<Record<string, unknown>>;
  };
  evidenceIndex: Record<string, EvidenceIndexItem>;
  viewSchemaVersion: "screening_result_view_v2_0";
}

export interface ScreeningAssessment {
  screeningAssessmentId: string;
  applicationId: string;
  sourceBundleRef: string;
  scoreStatus: "scored" | "failed" | "pending";
  summary: string;
  screeningResultView?: ScreeningResultV2;
  versionMetadata: {
    bundleVersion: string;
    generatedAt: string;
    source: string;
  };
}

export type HardScreeningRequirementStatus =
  | "passed"
  | "failed"
  | "manual_review"
  | "not_evaluated";

export interface HardScreeningRequirementResultView {
  ruleId: string;
  name: string;
  requirementText: string;
  status: HardScreeningRequirementStatus;
  reason: string;
  reasonCode: string | null;
  sourceQuotes: string[];
}

export interface HardScreeningCountsView {
  total: number;
  passed: number;
  failed: number;
  manualReview: number;
  notEvaluated: number;
}

export interface HardScreeningReviewView {
  resultId: string | null;
  policyId: string | null;
  status:
    | "not_configured"
    | "pending"
    | "running"
    | "passed"
    | "failed"
    | "manual_review";
  summary: string;
  /** 后端按本次冻结规则结果统计，前端不得自行计算。 */
  counts: HardScreeningCountsView;
  requirements: HardScreeningRequirementResultView[];
}

export interface ScreeningReviewReadModel {
  application: Application;
  candidate: Candidate;
  job: Job;
  /** 本次 V1 评估冻结的硬筛要求及逐项结果，不是岗位当前策略。 */
  hardScreening: HardScreeningReviewView;
  screeningResult: ScreeningResultV2;
  decisionOverview: CandidateDecisionOverviewView;
  evidenceIndex: Record<string, EvidenceIndexItem>;
  availableActions: string[];
  recoveryActions: RecoveryAction[];
  workflowStatus: {
    applicationId: string;
    jobId?: string;
    resumeSubmissionId?: string;
    workflowRunId?: string;
    workflowType?: string;
    runStatus: "not_started" | "queued" | "running" | "completed" | "failed";
    applicationStatus: string;
    stage: string;
    updatedAt: string;
    /** 旧初筛页也必须使用统一流程状态。 */
    process?: WorkflowProcess;
    error?: string;
  };
  viewSchemaVersion: "screening_review_v2";
}

export interface ScreeningAssessmentReadModel {
  summary: {
    total_score: number;
    job_fit_score: number;
    resume_experience_score: number;
    education_score: number | null;
    qualification_status: GateStatus;
    recommendation: string;
  };
  job_fit: {
    groups: JobFitReadItem[];
  };
  capability_performance: {
    frameworks: CapabilityFrameworkReadItem[];
  };
  verification_focus: {
    interview_targets: VerificationFocusReadItem[];
  };
  evidence_index: Record<string, EvidenceIndexItem>;
  available_actions: Array<"enter_first_interview" | "hold" | "manual_review" | "reject">;
}

export interface JobFitReadItem {
  id: string;
  name: string;
  score: number | null;
  level: string;
  level_description: string;
  summary: string;
  evidence_ids: string[];
}

export interface CapabilityIndicatorReadItem {
  id: string;
  name: string;
  score: number;
  level: number | null;
  level_description: string;
  conclusion: string;
  evidence_ids: string[];
}

export interface CapabilityFrameworkReadItem {
  id: string;
  name: string;
  score: number;
  level: number | null;
  conclusion: string;
  indicators: CapabilityIndicatorReadItem[];
}

export interface VerificationFocusReadItem {
  id: string;
  title: string;
  summary: string;
  priority: "high" | "medium" | "low";
  evidence_ids: string[];
  expected_evidence: string;
}

export interface DecisionFocusItem {
  focusId: string;
  title: string;
  priority: "high" | "medium" | "low";
  oneLineReason: string;
  currentConclusion: string;
  verificationAction: string;
  status: string;
  sourceRefs: string[];
  missingInformation: string;
}

export interface DecisionFocusCollection {
  items: DecisionFocusItem[];
}

export interface AssessmentCoverage {
  planned: number;
  fullyAssessed: number;
  partiallyAssessed: number;
  notAssessed: number;
  coverageRate: number;
}

export interface StageHandoff {
  recommendation: string;
  decisionReason: string;
  confirmedStrengths: string[];
  remainingItems: string[];
}

export interface HrCondition {
  conditionId: string;
  label: string;
  status: "positive" | "neutral" | "concern";
  value: string;
}

export interface DecisionSupport {
  schemaVersion: "decision_support_v2_0";
  decisionFocus: DecisionFocusCollection;
  assessmentCoverage: AssessmentCoverage;
  stageHandoff?: StageHandoff | null;
  hrConditions: HrCondition[];
}

export type DecisionSummaryStage = "screening" | "after_first_interview" | "after_second_interview";
export type DecisionSummarySourceType = "score" | "capability" | "evidence" | "interview_evidence";
export type DecisionRecommendationLevel =
  | "strongly_recommend"
  | "recommend"
  | "cautious_recommend"
  | "not_recommend"
  | "strongly_not_recommend";

export interface DecisionSummarySourceRef {
  type: DecisionSummarySourceType;
  id: string;
}

export interface DecisionSummaryItem {
  text: string;
  sourceRefs: DecisionSummarySourceRef[];
}

export interface DecisionSummary {
  summaryVersion: string;
  stage: DecisionSummaryStage;
  recommendation: {
    level: DecisionRecommendationLevel;
    reason: string;
  };
  strengths: DecisionSummaryItem[];
  risks: DecisionSummaryItem[];
  generatedAt: string;
  generationMode: "llm" | "rule_fallback";
  sourceSnapshotHash: string;
  decisionFocusGroups?: Array<{
    title: string;
    reason: string;
    verificationAction: string;
    sourceSignalIds: string[];
  }>;
}

export interface DecisionAssessmentView {
  totalScore: number | null;
  jobFitScore: number | null;
  experienceScore: number | null;
  educationScore: number | null;
  qualificationStatus: GateStatus;
}

export interface DecisionSummaryItemView {
  title: string;
  summary: string;
  sourceSignalIds: string[];
  evidenceIds: string[];
}

export interface AiSummaryView {
  recommendationLevel: DecisionRecommendationLevel;
  recommendationReason: string;
  strengths: DecisionSummaryItemView[];
  weaknesses: DecisionSummaryItemView[];
  generationMode: "llm" | "rule_fallback";
}

export interface VerificationFocusItemView {
  focusId?: string;
  focusType: string;
  title: string;
  reason: string;
  verificationGoal: string;
  sourceInterviewTargetIds: string[];
  status: "open" | "resolved";
  evidenceIds: string[];
}

export interface VerificationFocusView {
  items: VerificationFocusItemView[];
  generationMode: "llm" | "rule_fallback";
}

export interface CandidateDecisionOverviewView {
  /** 后端已发布的实际 AAV 阶段，卡片标题不得依据所在页面猜测。 */
  assessmentStage: AssessmentStage;
  assessment: DecisionAssessmentView;
  aiSummary: AiSummaryView;
  verificationFocus: VerificationFocusView;
  sourceSnapshotHash: string;
  generatedAt: string;
}

/** 已发布 AAV 的 V2/V3 变化展示合同；前端不读取原始 JSON，也不自行计算变化。 */
export type AssessmentStage = "screening" | "after_first_interview" | "after_second_interview";
export type SignalChangeStatus = "added" | "retained" | "closed";
export type InterviewTargetStatus = "open" | "resolved";
/** 后端 core_result_json 映射到页面前的分数变化 DTO。 */
export interface AssessmentScoreChange {
  metric: "total" | "job_fit" | "experience" | "education";
  previousValue?: number | null;
  currentValue?: number | null;
  delta?: number | null;
}

/** 受本轮面评影响的能力项变化；页面只展示，不重新计算。 */
export interface AssessmentCapabilityChange {
  resultRef: string;
  capabilityId: string;
  previousScore?: number | null;
  currentScore?: number | null;
  delta?: number | null;
  reasonCode: string;
  evidenceIds: string[];
}
/** V2/V3 的变化项取前后版本并集；V1 不传 changeStatus。 */
export interface AssessmentSignal {
  signalKey: string;
  changeStatus?: SignalChangeStatus;
  title: string;
  summary: string;
  currentRank?: number;
  previousRank?: number;
  evidenceIds: string[];
}

/** status=open/resolved 分别展示为“仍待确认/已关闭”，不再存在冲突或人工复核状态。 */
export interface AssessmentTarget {
  targetId: string;
  status: InterviewTargetStatus;
  title: string;
  reason: string;
  goal: string;
  evidenceIds: string[];
}

/**
 * 嵌入既有二面审核 / 最终审核页的 V2、V3 变化读模型。
 * 它只表达已经发布评估版本相对前一版的结果，前端不得据此重新计算评分。
 */
export interface AssessmentChangeSet {
  assessmentVersionId: string;
  stage: AssessmentStage;
  previousAssessmentVersionId?: string;
  scoreChanges: AssessmentScoreChange[];
  capabilityChanges: AssessmentCapabilityChange[];
  roundChangeSummary?: AssessmentRoundChange | null;
  strengths: AssessmentSignal[];
  weaknesses: AssessmentSignal[];
  verificationFocus: AssessmentTarget[];
}

export interface AssessmentRoundChange {
  title: string;
  summary: string;
  scoreChanges: AssessmentScoreChange[];
  capabilityChanges: AssessmentCapabilityChange[];
}
