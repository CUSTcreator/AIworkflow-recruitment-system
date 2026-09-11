import type { ApplicationStatus } from "@/modules/applications/contracts";
import type { Role } from "@/modules/auth/contracts";
import type {
  DecisionSummary,
  EvidenceIndexItem,
  EvidenceStrength,
  ScreeningAssessmentReadModel,
  VerificationFocusReadItem
} from "@/modules/assessment/contracts";

export type InterviewRound = "first" | "second";
export type InterviewGuideType = "technical_first_round" | "hr_second_round";
export type ConfirmedAssessmentType = "technical_first_round_feedback" | "hr_second_round_feedback";
export type AiDraftItemStatus = "accepted" | "edited" | "rejected" | "inaccurate";

export interface VerificationTarget {
  targetId: string;
  round: InterviewRound;
  requirementId: string;
  title: string;
  unknownPoint: string;
  sourceEvidenceIds: string[];
  priority: "high" | "medium" | "low";
  selected: boolean;
  // 题单确认页暂不展开这些字段，但保存、确认和一面后解析必须完整透传。
  purpose?: "verify_experience" | "elicit_missing" | "direct_demonstration";
  targetType?: "job_capability" | "preset_indicator";
  triggerCode?: string;
}

export interface InterviewQuestion {
  questionId: string;
  verificationTargetIds: string[];
  mainQuestion: string;
  followUpQuestions: string[];
  expectedEvidence: string[];
  negativeSignals: string[];
  priority: "high" | "medium" | "low";
  confirmed: boolean;
  sourceType?: "ai_suggestion" | "interviewer_custom" | "common_template";
  sectionType?: "technical" | "common";
  resultType?: "capability" | "non_scoring";
  evaluationPoints?: string[];
  required?: boolean;
  templateVersionId?: string;
  sourceSuggestionId?: string;
  finalText?: string;
  isEdited?: boolean;
  finalQuestionId?: string;
  recommendation?: "high" | "medium" | "low";
  validationGoal?: string;
  // 题单流程的内部字段：由后端规则层冻结，前端编辑题干时必须原样保留。
  slotId?: string;
  probeAngle?: string;
  questionType?: "experience_probe" | "capability_probe" | "direct_task";
  purpose?: "verify_experience" | "elicit_missing" | "direct_demonstration";
  expectedResultType?: "experience_fact" | "interview_performance";
  scenarioId?: string | null;
  targetJobCapabilityIds?: string[];
  targetPresetIndicatorIds?: string[];
  sourceEvidenceIds?: string[];
  evaluationRubrics?: Array<{
    rubricId: string;
    targetType: "job_capability" | "preset_indicator";
    targetId: string;
    maxLevel: number;
    levelAnchors: Array<{ level: number; description: string }>;
  }>;
}

export interface InterviewPlan {
  planId: string;
  applicationId: string;
  // 后端 FirstInterviewPlanVersion 的展示元数据；保存草稿时随原字段回传以校验是否仍是最新版本。
  guideVersion?: string;
  planVersionId?: string;
  planVersion?: number;
  planStatus?: "draft" | "superseded" | "confirmed";
  sourceAssessmentVersionId?: string;
  round: InterviewRound;
  guideType?: InterviewGuideType;
  goal: string;
  durationMinutes: number;
  questionCount: number;
  targets: VerificationTarget[];
  questions: InterviewQuestion[];
  technicalQuestions?: InterviewQuestion[];
  commonQuestions?: InterviewQuestion[];
  sections?: Array<{
    sectionType: "technical" | "common";
    title: string;
    questions: InterviewQuestion[];
  }>;
  commonTemplate?: {
    templateId: string;
    templateVersionId: string;
    name: string;
    version: number;
    isDefault: boolean;
  };
  commonTemplateStatus?: "ready" | "missing";
  commonTemplateVersion?: number;
  commonTemplateVersionId?: string;
  questionSuggestions?: InterviewQuestion[];
  recommendedQuestionIds?: string[];
  coverage?: {
    targetCount: number;
    coveredTargetIds: string[];
    uncoveredTargetIds: string[];
    questionCount: number;
    recommendedQuestionCount: number;
  };
  planningSummary?: string;
  generationMode?: "llm" | "rule_fallback";
  generationWarnings?: string[];
  generatedAt?: string;
  interviewerNotes?: string;
  confirmed: boolean;
  guideStatus?: "draft" | "confirmed";
  draftRevision?: number;
  updatedAt?: string;
  updatedBy?: string;
  confirmedAt?: string;
}

export interface FirstInterviewPlanningReadModel {
  screening_summary: ScreeningAssessmentReadModel["summary"];
  verification_focus: { items: VerificationFocusReadItem[] };
  question_suggestions: InterviewQuestion[];
  draft_guide: InterviewPlan;
  coverage_check: {
    covered: number;
    total: number;
    uncovered: string[];
    duplicate_question_ids: string[];
    unlinked_question_ids: string[];
    concentration_warning?: string;
  };
  available_actions: Array<"save_draft" | "undo" | "confirm">;
}

export interface QuestionResponse {
  questionResponseId: string;
  applicationId: string;
  interviewId: string;
  interviewRound: InterviewRound;
  questionId: string;
  answerSummary: string;
  interviewerNote: string;
  confirmedByInterviewer: boolean;
}

export interface FirstInterviewProgressDraft {
  progressDraftId?: string;
  applicationId: string;
  guideId: string;
  rawNotes: string;
  questionResponses: QuestionResponse[];
  verificationTargetStatuses: Record<string, "unverified" | "partially_verified" | "verified">;
  createdAt?: string;
  updatedAt?: string;
}

export interface FirstInterviewWorkspaceReadModel {
  assessment_summary: ScreeningAssessmentReadModel["summary"];
  confirmed_guide: InterviewPlan;
  verification_focus: ScreeningAssessmentReadModel["verification_focus"];
  evidence_index: Record<string, EvidenceIndexItem>;
  progress: FirstInterviewProgressDraft;
}

export interface SecondInterviewProgressDraft {
  progressDraftId?: string;
  applicationId: string;
  rawNotes: string;
  createdAt?: string;
  updatedAt?: string;
}

export type InterviewRecommendation = "strong_recommend" | "recommend" | "hold" | "not_recommend";

export interface InterviewEvidence {
  interviewEvidenceId: string;
  applicationId: string;
  interviewRound: InterviewRound;
  requirementId: string;
  sourceQuestionResponseId?: string;
  sourceQuestionResponseIds?: string[];
  sourceRawNotesId?: string;
  sourceAssessmentId?: string;
  sourceAiDraftItemId?: string;
  evidenceType: "interview_explanation";
  polarity: "positive" | "negative" | "neutral";
  strength: EvidenceStrength;
  text: string;
}

export interface InterviewerRawNotes {
  rawNotesId: string;
  applicationId: string;
  interviewRound: InterviewRound;
  guideId: string;
  authorRole: Role;
  authorName: string;
  content: string;
  createdAt: string;
  updatedAt: string;
}

export interface AiStructuredDraftItem {
  draftItemId: string;
  category: "technical_fact" | "risk_mapping" | "hr_readable_summary" | "hr_dimension" | "final_summary";
  title: string;
  aiText: string;
  confirmedText: string;
  status: AiDraftItemStatus;
  relatedRequirementIds: string[];
  relatedRiskIds: string[];
  sourceRawNotesId?: string;
  sourceQuestionResponseIds?: string[];
  reviewerNote?: string;
}

export interface AiStructuredDraft {
  draftId: string;
  applicationId: string;
  interviewRound: InterviewRound;
  sourceRawNotesId: string;
  generatedAt: string;
  items: AiStructuredDraftItem[];
}

export interface ConfirmedInterviewAssessment {
  assessmentId: string;
  applicationId: string;
  type: ConfirmedAssessmentType;
  interviewRound: InterviewRound;
  rawNotesId: string;
  aiDraftId: string;
  summary: string;
  overallRecommendation: string;
  decisionReason?: string;
  strengths?: string[];
  concerns?: string[];
  confirmedByRole: Role;
  confirmedByName: string;
  confirmedAt: string;
  aiDraftItems: AiStructuredDraftItem[];
  evidenceIds: string[];
  hrReadableSummary?: string;
}

export interface TechnicalVerificationItem {
  targetId: string;
  title: string;
  screeningStatus: string;
  firstRoundStatus: string;
  businessMeaning: string;
  remainingQuestion: string;
  hrActionRecommendation: string;
}

export interface HrSecondReviewPackage {
  packageId: string;
  applicationId: string;
  sourceAssessmentId: string;
  generatedAt: string;
  executiveSummary: string;
  technicalVerification: TechnicalVerificationItem[];
  hrFocusItems: string[];
  recommendation: string;
  decisionOptions: Array<"进入 HR 二面" | "暂缓" | "不推进" | "补充验证">;
  aiDecisionSummary?: DecisionSummary;
}

export interface HrInterviewAssessment {
  assessmentId: string;
  applicationId: string;
  interviewRound: "second";
  rawNotesId: string;
  aiDraftId: string;
  summary: string;
  overallRecommendation?: InterviewRecommendation;
  decisionReason?: string;
  strengths?: string[];
  concerns?: string[];
  technicalFollowupNeeded: boolean;
  technicalFollowupItems: string[];
  confirmedByName: string;
  confirmedAt: string;
}

export interface FinalCandidateReviewPackage {
  packageId: string;
  applicationId: string;
  generatedAt: string;
  summary: string;
  resumeEvidenceSummary: string[];
  technicalFirstRoundEvidence: string[];
  hrSecondRoundEvidence: string[];
  remainingVerificationItems: string[];
  humanDecisionChecklist: string[];
  recommendedDecision: "推进录用" | "不推进" | "补充验证";
  afterFirstScoreSnapshot?: Record<string, unknown>;
  afterSecondScoreSnapshot?: Record<string, unknown>;
  aiDecisionSummary?: DecisionSummary;
}

export interface HumanDecision {
  decisionId: string;
  applicationId: string;
  actorRole: Role;
  actorName: string;
  decision: string;
  decisionStage?: "screening" | "first_interview" | "hr_review" | "second_interview" | "final_review";
  fromStatus: ApplicationStatus;
  toStatus: ApplicationStatus;
  reason: string;
  createdAt: string;
}
