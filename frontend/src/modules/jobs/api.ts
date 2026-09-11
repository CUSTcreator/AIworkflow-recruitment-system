import { requestJson } from "@/shared/api/httpClient";
import type { WorkflowExecutionEvent, WorkflowProcess } from "@/shared/workflows/process";
import type { RecoveryAction } from "@/shared/recovery/actions";
import type { JobAssignmentRole } from "@/modules/jobs/jobConfiguration";

export type JobProfileStatus = "queued" | "processing" | "ready" | "review_required" | "failed" | "not_started";

export interface JobProfileRunResult {
  jobId: string;
  jdVersionId: string;
  workflowRunId: string;
  profileStatus: Exclude<JobProfileStatus, "not_started">;
  action: "retry" | "regenerate";
}

export interface ManagedJob {
  jobId: string;
  title: string;
  departmentId: string;
  departmentName: string;
  headcount?: number;
  status: string;
  hiringManagerName?: string;
  departmentRecruiterName?: string;
  jobConfigurationStatus: "complete" | "incomplete";
  missingJobAssignments: JobAssignmentRole[];
  candidateCount: number;
  sourceDocumentId?: string;
  sourceFilename?: string;
  sourceType: "file_import" | "untracked";
  jdText: string;
  responsibilities: string[];
  qualifications: string[];
  educationRequirement?: string;
  majorRequirement?: string;
  openedAt: string;
  closedAt?: string;
  jdVersionId?: string;
  jdVersion?: number;
  profileStatus: JobProfileStatus;
  activeJobProfileId?: string;
  profileWorkflowRunId?: string;
  profileErrorMessage?: string;
  profileDegraded: boolean;
  profileQualityMessage?: string;
  recoveryCode?: string;
  recoveryContext?: Record<string, unknown>;
  canRouteCandidate: boolean;
  canStartScreening: boolean;
  waitingApplicationCount: number;
  availableActions: RecoveryAction[];
}

export interface JobUpdateInput {
  title: string;
  headcount?: number;
  jdText: string;
  responsibilities: string[];
  qualifications: string[];
  educationRequirement?: string;
  majorRequirement?: string;
}
export type ImportRecordType = "job" | "resume";
export type ImportRecordStatus = "processing" | "review_required" | "completed" | "failed";

export interface ImportRecord {
  importId: string;
  importType: ImportRecordType;
  filename: string;
  displayName: string;
  status: string;
  resultSummary: string;
  jobId?: string;
  jobTitle?: string;
  applicationId?: string;
  targetIds: string[];
  screeningStatus?: string;
  errorMessage?: string;
  process?: WorkflowProcess;
  availableActions: RecoveryAction[];
  createdAt: string;
  updatedAt: string;
}

type RawManagedJob = {
  job_id: string; title: string; department_id: string; department_name: string;
  headcount?: number; status: string; hiring_manager_name?: string; department_recruiter_name?: string;
  job_configuration_status?: "complete" | "incomplete"; missing_job_assignments?: JobAssignmentRole[];
  candidate_count: number; source_document_id?: string; source_filename?: string;
  source_type: "file_import" | "untracked"; jd_text: string; responsibilities: string[];
  qualifications: string[]; education_requirement?: string; major_requirement?: string;
  opened_at: string; closed_at?: string; jd_version_id?: string; jd_version?: number;
  profile_status?: JobProfileStatus; active_job_profile_id?: string; profile_workflow_run_id?: string;
  profile_error_message?: string; profile_degraded?: boolean; profile_quality_message?: string;
  can_route_candidate?: boolean; can_start_screening?: boolean;
  recovery_code?: string; recovery_context?: Record<string, unknown>;
  waiting_application_count?: number; available_actions?: RecoveryAction[];
};

type RawImportRecord = {
  import_id: string; import_type: ImportRecordType; filename: string; display_name: string;
  status: string; result_summary: string; job_id?: string; job_title?: string;
  application_id?: string; target_ids: string[];
  screening_status?: string; error_message?: string; process?: WorkflowProcess;
  available_actions?: RecoveryAction[];
  created_at: string; updated_at: string;
};

function managedJob(item: RawManagedJob): ManagedJob {
  return {
    jobId: item.job_id, title: item.title, departmentId: item.department_id,
    departmentName: item.department_name, headcount: item.headcount, status: item.status,
    hiringManagerName: item.hiring_manager_name, departmentRecruiterName: item.department_recruiter_name,
    jobConfigurationStatus: item.job_configuration_status ?? "complete",
    missingJobAssignments: item.missing_job_assignments ?? [],
    candidateCount: item.candidate_count, sourceDocumentId: item.source_document_id,
    sourceFilename: item.source_filename, sourceType: item.source_type, jdText: item.jd_text,
    responsibilities: item.responsibilities, qualifications: item.qualifications,
    educationRequirement: item.education_requirement, majorRequirement: item.major_requirement,
    openedAt: item.opened_at, closedAt: item.closed_at, jdVersionId: item.jd_version_id,
    jdVersion: item.jd_version, profileStatus: item.profile_status ?? "not_started",
    activeJobProfileId: item.active_job_profile_id, profileWorkflowRunId: item.profile_workflow_run_id,
    profileErrorMessage: item.profile_error_message, profileDegraded: Boolean(item.profile_degraded),
    profileQualityMessage: item.profile_quality_message, canRouteCandidate: Boolean(item.can_route_candidate),
    recoveryCode: item.recovery_code, recoveryContext: item.recovery_context,
    canStartScreening: Boolean(item.can_start_screening),
    waitingApplicationCount: item.waiting_application_count ?? 0,
    availableActions: item.available_actions ?? []
  };
}

export async function getManagedJobs(token: string): Promise<ManagedJob[]> {
  return (await requestJson<RawManagedJob[]>(token, "/job-management/jobs")).map(managedJob);
}

export function deleteManagedJob(token: string, jobId: string) {
  return requestJson<{ jobId: string; deleted: boolean; deletedAt: string }>(
    token, `/job-management/jobs/${jobId}`,
    { method: "DELETE", headers: { "Idempotency-Key": crypto.randomUUID() } }
  );
}

export function requestJobProfileRun(
  token: string, jobId: string, action: JobProfileRunResult["action"], reason?: string,
): Promise<JobProfileRunResult> {
  return requestJson<{
    job_id: string; jd_version_id: string; workflow_run_id: string;
    profile_status: JobProfileRunResult["profileStatus"]; action: JobProfileRunResult["action"];
  }>(token, `/job-management/jobs/${jobId}/profile-runs/${action}`, {
    method: "POST",
    headers: { "Idempotency-Key": crypto.randomUUID() },
    body: JSON.stringify({ reason: reason ?? null })
  }).then((item) => ({
    jobId: item.job_id, jdVersionId: item.jd_version_id, workflowRunId: item.workflow_run_id,
    profileStatus: item.profile_status, action: item.action
  }));
}

export function getJobProfileWorkflowTimeline(
  token: string, jobId: string, workflowRunId: string,
): Promise<{ items: WorkflowExecutionEvent[] }> {
  return requestJson(token, `/job-management/jobs/${jobId}/profile-runs/${workflowRunId}/timeline`);
}

export async function getImportRecords(token: string, type?: ImportRecordType, status?: string): Promise<{ items: ImportRecord[]; total: number; unreadActionCount: number }> {
  const params = new URLSearchParams({ limit: "200" });
  if (type) params.set("type", type);
  if (status) params.set("status", status);
  const result = await requestJson<{ items: RawImportRecord[]; total: number; unread_action_count: number }>(token, `/import-records?${params}`);
  return {
    total: result.total, unreadActionCount: result.unread_action_count,
    items: result.items.map((item) => ({
      importId: item.import_id, importType: item.import_type, filename: item.filename,
      displayName: item.display_name, status: item.status, resultSummary: item.result_summary,
      jobId: item.job_id, jobTitle: item.job_title, applicationId: item.application_id,
      targetIds: item.target_ids, screeningStatus: item.screening_status,
      errorMessage: item.error_message, process: item.process,
      availableActions: item.available_actions ?? [],
      createdAt: item.created_at, updatedAt: item.updated_at
    }))
  };
}

export function markImportRecordsRead(token: string, type: ImportRecordType): Promise<{ import_type: ImportRecordType; last_read_at: string }> {
  return requestJson(token, `/import-records/read?type=${type}`, { method: "POST" });
}

export async function updateManagedJob(token: string, jobId: string, input: JobUpdateInput): Promise<ManagedJob> {
  const item = await requestJson<RawManagedJob>(token, `/job-management/jobs/${jobId}`, {
    method: "PATCH",
    headers: { "Idempotency-Key": crypto.randomUUID() },
    body: JSON.stringify({
      title: input.title, headcount: input.headcount ?? null, jd_text: input.jdText,
      responsibilities: input.responsibilities, qualifications: input.qualifications,
      education_requirement: input.educationRequirement ?? null,
      major_requirement: input.majorRequirement ?? null
    })
  });
  return managedJob(item);
}
