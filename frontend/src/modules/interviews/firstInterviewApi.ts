import type {
  Application,
  Candidate,
  Job
} from "@/modules/applications/contracts";
import type {
  CandidateDecisionOverviewView,
  DecisionSupport,
  DecisionSummary,
  EvidenceIndexItem,
  HardScreeningReviewView,
  ScreeningAssessment
} from "@/modules/assessment/contracts";
import type { FirstInterviewProgressDraft, InterviewPlan } from "./contracts";
import { requestJson } from "@/shared/api/httpClient";
import type { RecoveryAction } from "@/shared/recovery/actions";

/**
 * 一面页面读取 DTO。plan 是后端 FirstInterviewPlanPresentationResult 经读模型映射后的结果；
 * 页面只能把题目内部字段完整回传，不能据此重新计算题型、Target 绑定或 Rubric。
 */
export interface FirstInterviewBaseReadModel {
  application: Application;
  candidate: Candidate;
  job: Job;
  hardScreening: HardScreeningReviewView;
  screening: ScreeningAssessment;
  plan?: InterviewPlan;
  planningState?: {
    status: "not_started" | "pending" | "running" | "ready" | "failed" | "blocked";
    workflowRunId?: string;
    planVersionId?: string;
    sourceAssessmentVersionId?: string;
    generationMode?: "llm" | "rule_fallback" | "manual_fallback";
    error?: string;
    recoveryCode?: string;
    recoveryMessage?: string;
  };
  progressDraft?: FirstInterviewProgressDraft;
  decisionSummary?: DecisionSummary;
  decisionOverview: CandidateDecisionOverviewView;
  decisionSupport: DecisionSupport;
  evidenceIndex: Record<string, EvidenceIndexItem>;
  availableActions: string[];
  recoveryActions: RecoveryAction[];
  workflowStatus: Record<string, unknown>;
}

export interface FirstInterviewPlanReadModel extends FirstInterviewBaseReadModel {
  viewSchemaVersion: "first_interview_plan_v3";
}

export interface FirstInterviewWorkspaceReadModel extends FirstInterviewBaseReadModel {
  viewSchemaVersion: "first_interview_workspace_v2";
}

export function getFirstInterviewPlanView(
  token: string,
  applicationId: string
): Promise<FirstInterviewPlanReadModel> {
  return requestJson(token, `/applications/${applicationId}/views/first-interview-plan`);
}

export function getFirstInterviewWorkspaceView(
  token: string,
  applicationId: string
): Promise<FirstInterviewWorkspaceReadModel> {
  return requestJson(token, `/applications/${applicationId}/views/first-interview-workspace`);
}

