import type {
  Application,
  Candidate,
  Job
} from "@/modules/applications/contracts";
import type {
  AssessmentChangeSet,
  CandidateDecisionOverviewView,
  DecisionSupport,
  DecisionSummary,
  EvidenceIndexItem,
  HardScreeningReviewView,
  ScreeningAssessment
} from "@/modules/assessment/contracts";
import type {
  ConfirmedInterviewAssessment,
  FinalCandidateReviewPackage,
  HrInterviewAssessment,
  HrSecondReviewPackage,
  InterviewEvidence,
  InterviewPlan,
  SecondInterviewProgressDraft
} from "./contracts";
import { requestJson } from "@/shared/api/httpClient";
import type { NonCapabilityCardView } from "./components/NonCapabilityCard";
import type { RecoveryAction } from "@/shared/recovery/actions";

export interface InterviewRawNoteView {
  content: string;
  authorName: string;
  createdAt: string;
}

export interface RecordedQuestionView {
  questionId: string;
  questionText: string;
  answerSummary: string;
  interviewerNote: string;
}

export interface FirstInterviewOriginalRecord {
  rawNotes?: InterviewRawNoteView;
  recordedQuestions: RecordedQuestionView[];
}

export interface SecondInterviewOriginalRecord {
  rawNotes?: InterviewRawNoteView;
}

export interface SecondInterviewBaseReadModel {
  application: Application;
  candidate: Candidate;
  job: Job;
  hardScreening: HardScreeningReviewView;
  screening: ScreeningAssessment;
  plan?: InterviewPlan;
  progressDraft?: SecondInterviewProgressDraft;
  reviewPackage?: HrSecondReviewPackage;
  finalPackage?: FinalCandidateReviewPackage;
  hrAssessment?: HrInterviewAssessment;
  firstAssessment?: ConfirmedInterviewAssessment;
  firstEvidence: InterviewEvidence[];
  firstOriginalRecord?: FirstInterviewOriginalRecord;
  secondOriginalRecord?: SecondInterviewOriginalRecord;
  screeningScoreSnapshot?: Record<string, unknown>;
  afterFirstScoreSnapshot?: Record<string, unknown>;
  afterSecondScoreSnapshot?: Record<string, unknown>;
  /** 后端从已发布 AAV 投影的 V2/V3 相对前一版变化。 */
  afterFirstAssessmentChanges?: AssessmentChangeSet | null;
  afterSecondAssessmentChanges?: AssessmentChangeSet | null;
  currentScoreSnapshot?: Record<string, unknown>;
  decisionSummary?: DecisionSummary;
  decisionOverview: CandidateDecisionOverviewView;
  decisionSupport: DecisionSupport;
  nonCapabilityCard: NonCapabilityCardView;
  evidenceIndex: Record<string, EvidenceIndexItem>;
  availableActions: string[];
  recoveryActions: RecoveryAction[];
  workflowStatus: Record<string, unknown>;
}

export interface SecondInterviewReviewReadModel extends SecondInterviewBaseReadModel {
  viewSchemaVersion: "second_interview_review_v2";
}

export interface SecondInterviewWorkspaceReadModel extends SecondInterviewBaseReadModel {
  viewSchemaVersion: "second_interview_workspace_v2";
}

export interface FinalReviewReadModel extends SecondInterviewBaseReadModel {
  viewSchemaVersion: "final_review_v2";
}

export function getSecondInterviewReviewView(
  token: string,
  applicationId: string
): Promise<SecondInterviewReviewReadModel> {
  return requestJson(token, `/applications/${applicationId}/views/hr-second-review`);
}

export function getSecondInterviewWorkspaceView(
  token: string,
  applicationId: string
): Promise<SecondInterviewWorkspaceReadModel> {
  return requestJson(token, `/applications/${applicationId}/views/second-interview-workspace`);
}

export function getFinalReviewView(
  token: string,
  applicationId: string
): Promise<FinalReviewReadModel> {
  return requestJson(token, `/applications/${applicationId}/views/final-review`);
}
