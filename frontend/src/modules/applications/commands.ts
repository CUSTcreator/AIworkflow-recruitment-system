import type { FinalDecision } from "./contracts";
import type {
  FirstInterviewProgressDraft,
  InterviewPlan,
  SecondInterviewProgressDraft
} from "@/modules/interviews/contracts";
import type {
  CompleteFirstInterviewRequest,
  CompleteSecondInterviewRequest
} from "@/modules/interviews/commands";
import { requestJson } from "@/shared/api/httpClient";

export interface InterviewPlanDraftResponse {
  draft_version: number;
  updated_at: string;
}

export interface InterviewProgressSaveResponse {
  draft_version: number;
  updated_at: string;
}

export interface ApplicationCommandResponse {
  application_id: string;
  status: string;
  message: string;
  next_route?: string;
  workflow_run_id?: string;
  run_status?: "pending" | "queued" | "running" | "completed" | "failed";
}

export type JobProfileRecoveryMode = "reprocess_frozen" | "adopt_current";

function idempotencyKey(action: string, applicationId: string): string {
  return [action, applicationId, crypto.randomUUID()].join("-");
}

function post(
  token: string,
  applicationId: string,
  path: string,
  action: string,
  body: unknown = {}
): Promise<ApplicationCommandResponse> {
  return requestJson(token, path, {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey(action, applicationId) },
    body: JSON.stringify(body)
  });
}

function put<T>(
  token: string,
  applicationId: string,
  path: string,
  action: string,
  body: unknown
): Promise<T> {
  return requestJson(token, path, {
    method: "PUT",
    headers: { "Idempotency-Key": idempotencyKey(action, applicationId) },
    body: JSON.stringify(body)
  });
}

export const applicationCommands = {
  runScoring: (token: string, applicationId: string) =>
    post(token, applicationId, `/applications/${applicationId}/workflows/scoring/run`, "run_scoring"),
  repairJobProfile: (
    token: string,
    applicationId: string,
    mode: JobProfileRecoveryMode = "reprocess_frozen",
  ) => post(
    token,
    applicationId,
    `/applications/${applicationId}/recovery/job-profile`,
    "repair_job_profile",
    { mode },
  ),
  runFirstInterviewPlanning: (token: string, applicationId: string) =>
    post(token, applicationId, `/applications/${applicationId}/workflows/first-interview-planning/run`, "run_first_interview_planning"),
  approveFirstInterview: (token: string, applicationId: string) =>
    post(token, applicationId, `/applications/${applicationId}/actions/approve-first-interview`, "approve_first_interview"),
  departmentDecision: (token: string, applicationId: string, decision: "暂缓" | "不推进" | "人工复核") =>
    post(token, applicationId, `/applications/${applicationId}/actions/department-decision`, "department_decision", { decision }),
  saveFirstInterviewPlanDraft: (token: string, applicationId: string, plan: InterviewPlan) =>
    put<InterviewPlanDraftResponse>(token, applicationId, `/applications/${applicationId}/interviews/first/plan-draft`, "save_first_guide_draft", { plan }),
  confirmFirstInterviewPlan: (token: string, applicationId: string, plan: InterviewPlan) =>
    post(token, applicationId, `/applications/${applicationId}/workflows/first-interview-planning/confirm`, "confirm_first_guide", { plan }),
  continueFirstInterviewManually: (token: string, applicationId: string) =>
    post(token, applicationId, `/applications/${applicationId}/actions/continue-first-interview-manually`, "continue_first_interview_manually"),

  saveFirstInterviewProgress: (token: string, applicationId: string, draft: FirstInterviewProgressDraft) =>
    put<InterviewProgressSaveResponse>(token, applicationId, `/applications/${applicationId}/interviews/first/progress`, "save_first_interview_progress", draft),
  completeFirstInterview: (token: string, applicationId: string, request: CompleteFirstInterviewRequest) =>
    post(token, applicationId, `/applications/${applicationId}/actions/complete-first-interview`, "complete_first_interview", request),
  retryPostFirstScoring: (token: string, applicationId: string) =>
    post(token, applicationId, `/applications/${applicationId}/workflows/post-first-scoring/retry`, "retry_post_first_scoring"),
  rebuildScreeningAssessment: (token: string, applicationId: string) =>
    post(token, applicationId, `/applications/${applicationId}/workflows/scoring/rebuild`, "rebuild_screening_assessment"),
  repairPostFirstScoring: (token: string, applicationId: string, rawNotes: string) =>
    post(token, applicationId, `/applications/${applicationId}/workflows/post-first-scoring/repair`, "repair_post_first_scoring", { rawNotes }),
  approveSecondInterview: (token: string, applicationId: string) =>
    post(token, applicationId, `/applications/${applicationId}/actions/approve-second-interview`, "approve_second_interview"),
  hrDecision: (token: string, applicationId: string, decision: "暂缓" | "不推进" | "补充验证") =>
    post(token, applicationId, `/applications/${applicationId}/actions/hr-decision`, "hr_decision", { decision }),
  saveSecondInterviewProgress: (token: string, applicationId: string, draft: SecondInterviewProgressDraft) =>
    put<InterviewProgressSaveResponse>(token, applicationId, `/applications/${applicationId}/interviews/second/progress`, "save_second_interview_progress", draft),
  completeSecondInterview: (token: string, applicationId: string, request: CompleteSecondInterviewRequest) =>
    post(token, applicationId, `/applications/${applicationId}/actions/complete-second-interview`, "complete_second_interview", request),
  retryPostSecondScoring: (token: string, applicationId: string) =>
    post(token, applicationId, `/applications/${applicationId}/workflows/post-second-scoring/retry`, "retry_post_second_scoring"),
  repairPostSecondScoring: (token: string, applicationId: string, rawNotes: string) =>
    post(token, applicationId, `/applications/${applicationId}/workflows/post-second-scoring/repair`, "repair_post_second_scoring", { rawNotes }),
  finalDecision: (token: string, applicationId: string, decision: FinalDecision) =>
    post(token, applicationId, `/applications/${applicationId}/actions/final-decision`, "final_decision", { decision })
};
