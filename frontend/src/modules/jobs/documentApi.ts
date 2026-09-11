import { requestJson } from "@/shared/api/httpClient";
import type { RecoveryAction } from "@/shared/recovery/actions";

export interface JobDocumentImportView {
  import_id: string;
  source_document_id: string;
  document_type: string;
  original_filename: string;
  import_status: string;
  error_message?: string;
  recovery_code?: string;
  recovery_context_json?: {
    sheets?: Array<{ name: string; rows: unknown[][] }>;
    requiredFields?: string[];
    sourcePreviewUnavailable?: boolean;
    [key: string]: unknown;
  };
  available_actions?: RecoveryAction[];
  parser_provider?: string;
  document_blocks_ref?: string;
  document_blocks_sha256?: string;
  document_blocks_schema_version?: string;
  created_at: string;
  updated_at: string;
}

export interface JobDocumentRepairRequest {
  action: "review_job_headers" | "review_job_fields";
  sheet_name?: string;
  header_row_index?: number;
  header_mapping?: Record<string, number>;
  fields?: Record<string, Record<string, unknown>>;
}

export type JobDraftAssistanceStatus = "ai_assisted" | "review_required";

export interface JobDraftFieldHint {
  field: "title" | "headcount" | "responsibilities" | "qualifications" | "education_requirement" | "major_requirement" | "department_id";
  status: JobDraftAssistanceStatus;
  message: string;
}

export interface JobDraftExtractionAssistance {
  status: JobDraftAssistanceStatus;
  field_hints: JobDraftFieldHint[];
}

export type JobDraftHardScreeningCriterion = "minimum_degree" | "highest_education_status" | "highest_education_graduation_year" | "minimum_experience_years" | "project_experience" | "required_skill" | "certification" | "custom";

export interface JobDraftHardScreeningPreview {
  criterion_type: JobDraftHardScreeningCriterion;
  name: string;
  expected_value: string | number;
  description: string;
  enabled: boolean;
}

export interface JobDraftView {
  job_draft_id: string;
  source_document_id: string;
  sequence_no: number;
  title: string;
  headcount?: number;
  responsibilities: string[];
  qualifications: string[];
  education_requirement?: string;
  major_requirement?: string;
  department_id?: string;
  source_department_name?: string;
  department_match_status: "matched" | "unmatched" | "ambiguous" | "manually_resolved" | "auto_create" | "auto_created" | "inactive";
  source_text: string;
  status: string;
  confirmed_job_id?: string;
  duplicate_status: "new_job" | "existing_job" | "same_upload";
  existing_job_id?: string;
  duplicate_group_id?: string;
  resolution?: "create" | "overwrite" | "skip" | "keep";
  preset_model_id: "engineering_experience" | "general_professional_experience";
  preset_model_version: string;
  recommended_preset_model_id: "engineering_experience" | "general_professional_experience";
  updated_at: string;
  extraction_assistance?: JobDraftExtractionAssistance;
  hard_screening_preview?: JobDraftHardScreeningPreview[];
  available_actions?: RecoveryAction[];
}

export interface JobDraftPatch {
  preset_model_id?: "engineering_experience" | "general_professional_experience";
  title?: string;
  headcount?: number;
  responsibilities?: string[];
  qualifications?: string[];
  education_requirement?: string;
  major_requirement?: string;
  department_id?: string;
  resolution?: "create" | "overwrite" | "skip" | "keep";
  hard_screening_rules?: JobDraftHardScreeningPreview[];
}

export interface ConfirmJobDraftsResponse {
  document_id: string;
  import_status: string;
  jobs: Array<{
    job_id?: string;
    job_draft_id: string;
    title: string;
    reused: boolean;
    skipped: boolean;
    jd_version_id?: string;
    profile_status?: "queued" | "processing" | "ready" | "failed" | "review_required";
    profile_workflow_run_id?: string;
  }>;
}

export function uploadJobDocument(token: string, file: File): Promise<JobDocumentUploadResponse> {
  const form = new FormData();
  form.append("file", file);
  return requestJson(token, "/job-documents", { method: "POST", headers: { "Idempotency-Key": crypto.randomUUID() }, body: form });
}

export interface JobDocumentUploadResponse {
  document_id: string;
  import_id: string;
  workflow_run_id: string;
  import_status: string;
  reused: boolean;
}

export function getJobDocument(token: string, documentId: string): Promise<JobDocumentImportView> {
  return requestJson(token, `/job-documents/${documentId}`);
}

export function getJobDrafts(token: string, documentId: string): Promise<JobDraftView[]> {
  return requestJson(token, `/job-documents/${documentId}/drafts`);
}

export function updateJobDraft(token: string, documentId: string, draftId: string, patch: JobDraftPatch): Promise<JobDraftView> {
  return requestJson(token, `/job-documents/${documentId}/drafts/${draftId}`, {
    method: "PATCH",
    headers: { "Idempotency-Key": crypto.randomUUID() },
    body: JSON.stringify(patch)
  });
}

export interface DeleteJobDraftResponse {
  document_id: string;
  draft_id: string;
  draft_status: "deleted";
  import_status: string;
}

export function deleteJobDraft(token: string, documentId: string, draftId: string): Promise<DeleteJobDraftResponse> {
  return requestJson(token, `/job-documents/${documentId}/drafts/${draftId}`, { method: "DELETE", headers: { "Idempotency-Key": crypto.randomUUID() } });
}

export function confirmJobDrafts(
  token: string,
  documentId: string,
  drafts: Array<{ draft_id: string; hard_screening_rules: JobDraftHardScreeningPreview[] }>
): Promise<ConfirmJobDraftsResponse> {
  return requestJson(token, `/job-documents/${documentId}/confirm`, {
    method: "POST",
    headers: { "Idempotency-Key": crypto.randomUUID() },
    body: JSON.stringify({ drafts })
  });
}

export function retryJobDocument(token: string, documentId: string): Promise<JobDocumentUploadResponse> {
  return requestJson(token, `/job-documents/${documentId}/retry`, { method: "POST", headers: { "Idempotency-Key": crypto.randomUUID() } });
}

export function repairJobDocument(token: string, documentId: string, body: JobDocumentRepairRequest): Promise<JobDocumentUploadResponse> {
  return requestJson(token, `/job-documents/${documentId}/repair`, {
    method: "POST",
    headers: { "Idempotency-Key": crypto.randomUUID() },
    body: JSON.stringify(body)
  });
}
