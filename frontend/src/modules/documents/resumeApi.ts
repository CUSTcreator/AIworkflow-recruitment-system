import { requestJson } from "@/shared/api/httpClient";
import type { WorkflowExecutionEvent, WorkflowProcess } from "@/shared/workflows/process";
import type { RecoveryAction } from "@/shared/recovery/actions";
import { API_BASE } from "@/shared/api/httpClient";

export type ResumeSubmissionStatus =
  | "queued"
  | "parsing"
  | "extracting"
  | "review_required"
  | "failed"
  | "completed";

/** 简历任务的处理方式：初次上传、沿用文件重解析、上传替换文件或人工校正。 */
export type CandidateIntakeMode = "initial" | "reparse" | "replacement" | "manual_correction";

const resumeSubmissionStatuses = new Set<ResumeSubmissionStatus>([
  "queued",
  "parsing",
  "extracting",
  "review_required",
  "failed",
  "completed",
]);

function asResumeSubmissionStatus(value: unknown): ResumeSubmissionStatus {
  if (
    typeof value === "string" &&
    resumeSubmissionStatuses.has(value as ResumeSubmissionStatus)
  ) {
    return value as ResumeSubmissionStatus;
  }
  throw new Error("简历处理接口返回了不支持的状态值");
}
export interface JobOption {
  jobId: string;
  title: string;
  departmentId: string;
  departmentName: string;
  status: string;
}

export interface ManualRoutingJob {
  jobId: string;
  jdVersionId: string;
  jobProfileId?: string;
  title: string;
  departmentId: string;
  departmentName: string;
  jobStatus: "setup_pending" | "open";
  profileStatus: "queued" | "processing" | "ready" | "review_required" | "failed" | "not_started";
  isScreeningReady: boolean;
  selectionOutcome: "start_screening" | "wait_job_profile";
}

export interface ManualRoutingOptions {
  candidateId: string;
  candidateName: string;
  candidateMajor: string;
  jobs: ManualRoutingJob[];
}

export interface DuplicateCandidateOption {
  candidateId: string;
  displayName: string;
  school: string;
  major: string;
  applicationCount: number;
  activeApplicationCount: number;
  canReplace: boolean;
}

export interface DuplicateTarget {
  candidateId: string;
  displayName: string;
  applicationCount: number;
  activeApplicationCount: number;
  canReplace: boolean;
}

export interface CandidateIntakeApplicationAction {
  type: "navigate" | "view_hard_screening_result";
  label: string;
  route?: string;
}
export interface ResumeDocumentUploadResult {
  documentId: string;
  submissionId: string;
  workflowRunId: string;
  status: ResumeSubmissionStatus;
  reused: boolean;
  candidateId?: string;
  applicationId?: string;
  applicationIds: string[];
}

export interface ResumeSubmission {
  resumeSubmissionId: string;
  sourceDocumentId: string;
  jobId?: string;
  status: ResumeSubmissionStatus;
  candidateId?: string;
  applicationId?: string;
  errorMessage?: string;
}

export interface CandidateIntake {
  submissionId: string;
  submissionStatus: ResumeSubmissionStatus;
  intakeMode: CandidateIntakeMode;
  reviewKind?: string;
  failureKind?: string;
  recoveryCode?: string;
  duplicateTarget?: DuplicateTarget;
  candidate?: {
    candidate_id: string;
    display_name: string;
    status: string;
    major: string;
    resume_profile_id: string;
  };
  document?: {
    source_document_id: string;
    filename: string;
    available: boolean;
  };
  workflow?: {
    workflow_run_id: string;
    status: string;
    error_message?: string;
  };
  process?: WorkflowProcess;
  routing: Record<string, unknown>;
  routingStatus: string;
  routingReason?: string;
  applications: Array<{
    application_id: string;
    job_id: string;
    job_title: string;
    department_id: string;
    department_name: string;
    status: string;
    rejection_stage?: string;
    main_route: string;
    primary_action: {
      type: CandidateIntakeApplicationAction["type"];
      label: string;
      route?: string;
    };
    submitted_at: string;
    adopted_resume_submission_id?: string;
    uses_current_resume: boolean;
    major_requirement?: string;
  }>;
  reviewReason?: string;
  isCurrent: boolean;
  supersededBySubmissionId?: string;
  recovery: { issueCode?: string; displayMessage?: string; allowedActions: RecoveryAction[] };
  processingQuality: { status?: string; message?: string; parts?: string[] };
  availableActions: RecoveryAction[];
}

export interface WorkflowExecutionTimelineResponse {
  items: WorkflowExecutionEvent[];
}

export interface ResumeCorrectionSourceBlock {
  blockId: string;
  text: string;
  sourceLineStart?: number;
  sourceLineEnd?: number;
}

export interface ResumeCorrectionContextItem {
  contextId: string;
  contextType: string;
  text: string;
  sourceRefs: Array<Record<string, unknown>>;
}

export interface ResumeCorrectionSourceBullet {
  sourceBulletId: string;
  text: string;
  sourceRefs: Array<Record<string, unknown>>;
}

export interface ResumeCorrectionExperience {
  experienceUnitId: string;
  title: string;
  contextItems: ResumeCorrectionContextItem[];
  sourceBullets: ResumeCorrectionSourceBullet[];
  titleSourceRefs: Array<Record<string, unknown>>;
  contextSourceRefs: Array<Record<string, unknown>>;
  workSourceRefs: Array<Record<string, unknown>>;
}

export interface ResumeCorrectionDraft {
  submissionId: string;
  sourceBlocks: ResumeCorrectionSourceBlock[];
  candidateFacts: Record<string, unknown>;
  experienceUnits: ResumeCorrectionExperience[];
  skillClaims: Array<Record<string, unknown>>;
}

export interface ResumeManualCorrection {
  candidateFacts: Record<string, unknown>;
  experienceUnits: Array<Record<string, unknown>>;
  skillClaims: Array<Record<string, unknown>>;
}
export interface ResumeParsedContent {
  submissionId: string;
  filename: string;
  submissionStatus: ResumeSubmissionStatus;
  sourceAvailable: boolean;
  parsedText: string;
  parseMetadata: Record<string, unknown>;
  errorMessage?: string;
}

type RawJobOption = {
  job_id: string;
  title: string;
  department_id: string;
  department_name: string;
  status: string;
};

type RawUploadResult = {
  document_id: string;
  submission_id: string;
  workflow_run_id: string;
  status: string;
  reused: boolean;
  application_id?: string;
  application_ids?: string[];
  candidate_id?: string;
};

type RawSubmission = {
  resume_submission_id: string;
  source_document_id: string;
  job_id?: string;
  status: string;
  candidate_id?: string;
  application_id?: string;
  error_message?: string;
};

type RawResumeParsedContent = {
  submission_id: string;
  filename: string;
  submission_status: string;
  source_available: boolean;
  parsed_text: string;
  parse_result: Record<string, unknown>;
  error_message?: string;
};

export type CandidateIntakeStatus =
  "all" | "processing" | "review_required" | "failed" | "completed";

export interface CandidateIntakeListItem {
  submissionId: string;
  candidateId?: string;
  candidateName: string;
  candidateStatus?: string;
  candidateMajor: string;
  resumeProfileId: string;
  candidateDocumentsAvailable: boolean;
  filename: string;
  resumePdfUrl?: string;
  bucket: Exclude<CandidateIntakeStatus, "all">;
  stage: string;
  submissionStatus: ResumeSubmissionStatus;
  intakeMode: CandidateIntakeMode;
  reviewKind?: string;
  failureKind?: string;
  recoveryCode?: string;
  sourceAvailable: boolean;
  workflowStatus?: string;
  workflowRunId?: string;
  process?: WorkflowProcess;
  applications: Array<{
    applicationId: string;
    jobId: string;
    jobTitle: string;
    departmentId: string;
    departmentName: string;
    status: string;
    rejectionStage?: string;
    mainRoute: string;
    primaryAction: CandidateIntakeApplicationAction;
    submittedAt: string;
    adoptedResumeSubmissionId?: string;
    usesCurrentResume: boolean;
  }>;
  applicationCount: number;
  routingStatus: string;
  routingReason?: string;
  reviewReason?: string;
  isCurrent: boolean;
  supersededBySubmissionId?: string;
  recovery: { issueCode?: string; displayMessage?: string; allowedActions: RecoveryAction[] };
  processingQuality: { status?: string; message?: string; parts?: string[] };
  availableActions: RecoveryAction[];
  createdAt: string;
  updatedAt: string;
}

export interface CandidateIntakeCounts {
  processingCount: number;
  attentionCount: number;
  completedCount: number;
  totalCount: number;
  unreadAttentionCount: number;
}

export interface CandidateIntakeList {
  items: CandidateIntakeListItem[];
  total: number;
  page: number;
  pageSize: number;
  counts: CandidateIntakeCounts;
}

type RawCandidateIntakeList = {
  items: Array<{
    submission_id: string;
    candidate_id?: string;
    candidate_name: string;
    candidate_status?: string;
    candidate_major?: string;
    resume_profile_id?: string;
    candidate_documents_available?: boolean;
    filename: string;
    resume_pdf_url?: string;
    bucket: CandidateIntakeListItem["bucket"];
    stage: string;
    submission_status: string;
    intake_mode?: CandidateIntakeMode;
    review_kind?: string;
    failure_kind?: string;
    recovery_code?: string;
    source_available: boolean;
    workflow_status?: string;
    workflow_run_id?: string;
    process?: WorkflowProcess;
    applications?: Array<{
      application_id: string;
      job_id: string;
      job_title: string;
      department_id: string;
      department_name?: string;
      status: string;
      rejection_stage?: string;
      main_route?: string;
      primary_action?: {
        type: CandidateIntakeApplicationAction["type"];
        label: string;
        route?: string;
      };
      submitted_at?: string;
      adopted_resume_submission_id?: string;
      uses_current_resume?: boolean;
    }>;
    application_count: number;
    routing_status?: string;
    routing_reason?: string;
    review_reason?: string;
    is_current?: boolean;
    superseded_by_submission_id?: string;
    recovery?: { issue_code?: string; display_message?: string; allowed_actions?: RecoveryAction[] };
    processing_quality?: { status?: string; message?: string; parts?: string[] };
    available_actions?: RecoveryAction[];
    created_at: string;
    updated_at: string;
  }>;
  total: number;
  page: number;
  page_size: number;
  counts: {
    processing_count: number;
    attention_count: number;
    completed_count: number;
    total_count: number;
    unread_attention_count: number;
  };
};

function mapCandidateIntakeCounts(
  counts: RawCandidateIntakeList["counts"],
): CandidateIntakeCounts {
  return {
    processingCount: counts.processing_count,
    attentionCount: counts.attention_count,
    completedCount: counts.completed_count,
    totalCount: counts.total_count,
    unreadAttentionCount: counts.unread_attention_count,
  };
}

export async function getCandidateIntakeList(
  token: string,
  input: {
    status?: CandidateIntakeStatus;
    keyword?: string;
    page?: number;
    pageSize?: number;
  } = {},
): Promise<CandidateIntakeList> {
  const query = new URLSearchParams();
  if (input.status) query.set("status", input.status);
  if (input.keyword) query.set("keyword", input.keyword);
  if (input.page) query.set("page", String(input.page));
  if (input.pageSize) query.set("page_size", String(input.pageSize));
  const raw = await requestJson<RawCandidateIntakeList>(
    token,
    `/candidate-intakes${query.size ? `?${query}` : ""}`,
  );
  return {
    items: raw.items.map((item) => ({
      submissionId: item.submission_id,
      candidateId: item.candidate_id,
      candidateName: item.candidate_name,
      candidateStatus: item.candidate_status,
      candidateMajor: item.candidate_major ?? "",
      resumeProfileId: item.resume_profile_id ?? "",
      candidateDocumentsAvailable: Boolean(item.candidate_documents_available),
      filename: item.filename,
      resumePdfUrl: item.resume_pdf_url ? `${API_BASE.replace(/\/$/, "")}${item.resume_pdf_url.replace(/^\/api\/v1/, "")}` : undefined,
      bucket: item.bucket,
      stage: item.stage,
      submissionStatus: asResumeSubmissionStatus(item.submission_status),
      intakeMode: item.intake_mode ?? "initial",
      reviewKind: item.review_kind,
      failureKind: item.failure_kind,
      recoveryCode: item.recovery_code,
      sourceAvailable: item.source_available,
      workflowStatus: item.workflow_status,
      workflowRunId: item.workflow_run_id,
      process: item.process,
      applications: (item.applications ?? []).map((app) => ({
        applicationId: app.application_id,
        jobId: app.job_id,
        jobTitle: app.job_title,
        departmentId: app.department_id,
        departmentName: app.department_name ?? "",
        status: app.status,
        rejectionStage: app.rejection_stage,
        mainRoute: app.main_route ?? "/candidates",
        primaryAction: app.primary_action ?? {
          type: "navigate",
          label: "查看流程",
          route: app.main_route ?? "/candidates",
        },
        submittedAt: app.submitted_at ?? "",
        adoptedResumeSubmissionId: app.adopted_resume_submission_id,
        usesCurrentResume: Boolean(app.uses_current_resume),
      })),
      applicationCount: item.application_count,
      routingStatus: item.routing_status ?? "idle",
      routingReason: item.routing_reason,
      reviewReason: item.review_reason,
      isCurrent: Boolean(item.is_current),
      supersededBySubmissionId: item.superseded_by_submission_id,
      recovery: {
        issueCode: item.recovery?.issue_code,
        displayMessage: item.recovery?.display_message,
        allowedActions: item.recovery?.allowed_actions ?? [],
      },
      processingQuality: item.processing_quality ?? {},
      availableActions: item.available_actions ?? [],
      createdAt: item.created_at,
      updatedAt: item.updated_at,
    })),
    total: raw.total,
    page: raw.page,
    pageSize: raw.page_size,
    counts: mapCandidateIntakeCounts(raw.counts),
  };
}

export async function getCandidateIntakeSummary(
  token: string,
): Promise<CandidateIntakeCounts> {
  const raw = await requestJson<RawCandidateIntakeList["counts"]>(
    token,
    "/candidate-intakes/summary",
  );
  return mapCandidateIntakeCounts(raw);
}

export function markCandidateIntakesRead(token: string) {
  return requestJson<{ last_read_at: string }>(
    token,
    "/candidate-intakes/read",
    { method: "POST" },
  );
}
/** 归档 Candidate；历史简历与评估保留审计，但不再出现在业务列表或参与去重。 */
export function deleteCandidate(token: string, candidateId: string) {
  return requestJson<{
    candidate_id: string;
    deleted: boolean;
    already_deleted: boolean;
    application_count: number;
    cancelled_workflow_count: number;
  }>(token, `/candidates/${candidateId}`, {
    method: "DELETE",
    headers: { "Idempotency-Key": crypto.randomUUID() },
  });
}
export async function getActiveJobs(token: string): Promise<JobOption[]> {
  const items = await requestJson<RawJobOption[]>(token, "/jobs");
  return items.map((item) => ({
    jobId: item.job_id,
    title: item.title,
    departmentId: item.department_id,
    departmentName: item.department_name,
    status: item.status,
  }));
}

export async function uploadResumeDocument(
  token: string,
  input: { file: File; idempotencyKey?: string },
): Promise<ResumeDocumentUploadResult> {
  const body = new FormData();
  body.append("file", input.file);
  const result = await requestJson<RawUploadResult>(
    token,
    "/candidate-intakes/uploads",
    {
      method: "POST",
      body,
      headers: input.idempotencyKey
        ? { "Idempotency-Key": input.idempotencyKey }
        : undefined,
    },
  );
  return {
    documentId: result.document_id,
    submissionId: result.submission_id,
    workflowRunId: result.workflow_run_id,
    status: asResumeSubmissionStatus(result.status),
    reused: Boolean(result.reused),
    candidateId: result.candidate_id,
    applicationId: result.application_id,
    applicationIds: result.application_ids ?? [],
  };
}

export function decideResumeDuplicate(
  token: string,
  submissionId: string,
  decision: "replace_resume" | "discard_submission",
) {
  return requestJson<{
    submission_id: string;
    status: string;
    application_ids: string[];
  }>(token, `/candidate-intakes/${submissionId}/duplicate-decision`, {
    method: "POST",
    body: JSON.stringify({ decision }),
  });
}
export async function getAmbiguousDuplicateCandidates(
  token: string,
  submissionId: string,
): Promise<DuplicateCandidateOption[]> {
  const payload = await requestJson<{
    candidates?: Array<{
      candidate_id: string;
      display_name: string;
      school?: string;
      major?: string;
      application_count?: number;
      active_application_count?: number;
      can_replace?: boolean;
    }>;
  }>(token, `/candidate-intakes/${submissionId}/duplicate-candidates`);
  return (payload.candidates ?? []).map((candidate) => ({
    candidateId: candidate.candidate_id,
    displayName: candidate.display_name,
    school: candidate.school ?? "",
    major: candidate.major ?? "",
    applicationCount: candidate.application_count ?? 0,
    activeApplicationCount: candidate.active_application_count ?? 0,
    canReplace: Boolean(candidate.can_replace),
  }));
}

export function resolveAmbiguousDuplicate(
  token: string,
  submissionId: string,
  input: {
    decision: "merge_into_candidate" | "create_new_candidate" | "discard_submission";
    targetCandidateId?: string;
  },
) {
  return requestJson<{
    submission_id: string;
    status: ResumeSubmissionStatus;
    next_workflow_run_id?: string;
    application_ids: string[];
  }>(token, `/candidate-intakes/${submissionId}/duplicate-resolution`, {
    method: "POST",
    body: JSON.stringify({
      decision: input.decision,
      target_candidate_id: input.targetCandidateId,
    }),
  });
}
export async function getResumeSubmission(
  token: string,
  submissionId: string,
): Promise<ResumeSubmission> {
  const item = await requestJson<RawSubmission>(
    token,
    `/resume-documents/${submissionId}`,
  );
  return {
    resumeSubmissionId: item.resume_submission_id,
    sourceDocumentId: item.source_document_id,
    jobId: item.job_id ?? undefined,
    status: asResumeSubmissionStatus(item.status),
    candidateId: item.candidate_id,
    applicationId: item.application_id,
    errorMessage: item.error_message,
  };
}

export function getCandidateIntakeWorkflowTimeline(
  token: string,
  submissionId: string,
): Promise<WorkflowExecutionTimelineResponse> {
  return requestJson(token, `/candidate-intakes/${submissionId}/workflow-timeline`);
}

export async function getCandidateIntake(
  token: string,
  submissionId: string,
): Promise<CandidateIntake> {
  const item = await requestJson<{
    submission_id: string;
    submission_status: string;
    intake_mode?: CandidateIntakeMode;
    review_kind?: string;
    failure_kind?: string;
    recovery_code?: string;
    duplicate_target?: {
      candidate_id: string;
      display_name: string;
      application_count?: number;
      active_application_count?: number;
      can_replace?: boolean;
    };
    candidate?: CandidateIntake["candidate"];
    document?: CandidateIntake["document"];
    workflow?: CandidateIntake["workflow"];
    process?: WorkflowProcess;
    routing?: Record<string, unknown>;
    routing_status?: string;
    routing_reason?: string;
    applications?: CandidateIntake["applications"];
    review_reason?: string;
    is_current?: boolean;
    superseded_by_submission_id?: string;
    recovery?: { issue_code?: string; display_message?: string; allowed_actions?: RecoveryAction[] };
    processing_quality?: { status?: string; message?: string; parts?: string[] };
    available_actions?: RecoveryAction[];
  }>(token, `/candidate-intakes/${submissionId}`);
  return {
    submissionId: item.submission_id,
    submissionStatus: asResumeSubmissionStatus(item.submission_status),
    intakeMode: item.intake_mode ?? "initial",
    reviewKind: item.review_kind,
    failureKind: item.failure_kind,
    recoveryCode: item.recovery_code,
    duplicateTarget: item.duplicate_target
      ? {
          candidateId: item.duplicate_target.candidate_id,
          displayName: item.duplicate_target.display_name,
          applicationCount: item.duplicate_target.application_count ?? 0,
          activeApplicationCount:
            item.duplicate_target.active_application_count ?? 0,
          canReplace: Boolean(item.duplicate_target.can_replace),
        }
      : undefined,
    candidate: item.candidate,
    document: item.document,
    workflow: item.workflow,
    process: item.process,
    routing: item.routing ?? {},
    routingStatus: item.routing_status ?? "idle",
    routingReason: item.routing_reason,
    applications: item.applications ?? [],
    reviewReason: item.review_reason,
    isCurrent: Boolean(item.is_current),
    supersededBySubmissionId: item.superseded_by_submission_id,
    recovery: {
      issueCode: item.recovery?.issue_code,
      displayMessage: item.recovery?.display_message,
      allowedActions: item.recovery?.allowed_actions ?? [],
    },
    processingQuality: item.processing_quality ?? {},
    availableActions: item.available_actions ?? [],
  };
}

export async function getManualRoutingOptions(
  token: string,
  submissionId: string,
): Promise<ManualRoutingOptions> {
  const item = await requestJson<{
    candidate_id: string;
    candidate_name: string;
    candidate_major?: string;
    jobs?: Array<{
      job_id: string;
      jd_version_id?: string;
      job_profile_id?: string | null;
      title: string;
      department_id: string;
      department_name?: string;
      job_status?: "setup_pending" | "open";
      profile_status?: "queued" | "processing" | "ready" | "review_required" | "failed" | "not_started";
      is_screening_ready?: boolean;
      selection_outcome?: "start_screening" | "wait_job_profile";
    }>;
  }>(token, `/candidate-intakes/${submissionId}/routing-options`);
  return {
    candidateId: item.candidate_id,
    candidateName: item.candidate_name,
    candidateMajor: item.candidate_major ?? "",
    jobs: (item.jobs ?? []).map((job) => ({
      jobId: job.job_id,
      jdVersionId: job.jd_version_id ?? "",
      jobProfileId: job.job_profile_id ?? undefined,
      title: job.title,
      departmentId: job.department_id,
      departmentName: job.department_name ?? "",
      jobStatus: job.job_status ?? "open",
      profileStatus: job.profile_status ?? "not_started",
      isScreeningReady: Boolean(job.is_screening_ready),
      selectionOutcome: job.selection_outcome ?? (job.is_screening_ready ? "start_screening" : "wait_job_profile"),
    })),
  };
}

export function createApplicationsFromIntake(
  token: string,
  submissionId: string,
  jobIds: string[],
) {
  return requestJson<{ candidate_id: string; application_ids: string[] }>(
    token,
    `/candidate-intakes/${submissionId}/applications`,
    {
      method: "POST",
      body: JSON.stringify({ job_ids: jobIds }),
    },
  );
}

export async function getResumeCorrectionDraft(
  token: string,
  submissionId: string,
): Promise<ResumeCorrectionDraft> {
  const item = await requestJson<{
    submission_id: string;
    source_blocks?: Array<{ block_id: string; text: string; source_line_start?: number; source_line_end?: number }>;
    candidate_facts?: Record<string, unknown>;
    experience_units?: Array<{
      experience_unit_id?: string;
      title?: string;
      context_items?: Array<{
        context_id?: string;
        context_type?: string;
        text?: string;
        source_refs?: Array<Record<string, unknown>>;
      }>;
      source_bullets?: Array<{
        source_bullet_id?: string;
        text?: string;
        source_refs?: Array<Record<string, unknown>>;
      }>;
      title_source_refs?: Array<Record<string, unknown>>;
      context_source_refs?: Array<Record<string, unknown>>;
      work_source_refs?: Array<Record<string, unknown>>;
    }>;
    skill_claims?: Array<Record<string, unknown>>;
  }>(token, `/candidate-intakes/${submissionId}/correction-draft`);
  return {
    submissionId: item.submission_id,
    sourceBlocks: (item.source_blocks ?? []).map((block) => ({
      blockId: block.block_id,
      text: block.text,
      sourceLineStart: block.source_line_start,
      sourceLineEnd: block.source_line_end,
    })),
    candidateFacts: item.candidate_facts ?? {},
    experienceUnits: (item.experience_units ?? []).map((experience) => ({
      experienceUnitId: experience.experience_unit_id ?? "",
      title: experience.title ?? "",
      contextItems: (experience.context_items ?? []).map((context) => ({
        contextId: context.context_id ?? "",
        contextType: context.context_type ?? "other_context",
        text: context.text ?? "",
        sourceRefs: context.source_refs ?? [],
      })),
      sourceBullets: (experience.source_bullets ?? []).map((bullet) => ({
        sourceBulletId: bullet.source_bullet_id ?? "",
        text: bullet.text ?? "",
        sourceRefs: bullet.source_refs ?? [],
      })),
      titleSourceRefs: experience.title_source_refs ?? [],
      contextSourceRefs: experience.context_source_refs ?? [],
      workSourceRefs: experience.work_source_refs ?? [],
    })),
    skillClaims: item.skill_claims ?? [],
  };
}

export function submitResumeManualCorrection(
  token: string,
  submissionId: string,
  correction: ResumeManualCorrection,
) {
  return requestJson<{
    submission_id: string;
    workflow_run_id: string;
    status: ResumeSubmissionStatus;
    mode: CandidateIntakeMode;
  }>(token, `/candidate-intakes/${submissionId}/manual-correction`, {
    method: "POST",
    headers: { "Idempotency-Key": crypto.randomUUID() },
    body: JSON.stringify({
      candidate_facts: correction.candidateFacts,
      experience_units: correction.experienceUnits,
      skill_claims: correction.skillClaims,
    }),
  });
}

export function retryCandidateResumePublish(token: string, submissionId: string) {
  return requestJson<{
    submission_id: string;
    workflow_run_id: string;
    status: ResumeSubmissionStatus;
    mode: "publish_retry";
  }>(token, `/candidate-intakes/${submissionId}/retry-publish`, {
    method: "POST",
    headers: { "Idempotency-Key": crypto.randomUUID() },
  });
}

export function retryCandidateRouting(token: string, submissionId: string) {
  return requestJson<{
    submission_id: string;
    workflow_run_id: string;
    status: ResumeSubmissionStatus;
    mode: "routing_retry";
  }>(token, `/candidate-intakes/${submissionId}/retry-routing`, {
    method: "POST",
    headers: { "Idempotency-Key": crypto.randomUUID() },
  });
}
export async function getResumeParsedContent(
  token: string,
  submissionId: string,
): Promise<ResumeParsedContent> {
  const item = await requestJson<RawResumeParsedContent>(
    token,
    `/resume-documents/${submissionId}/parsed-content`,
  );
  return {
    submissionId: item.submission_id,
    filename: item.filename,
    submissionStatus: asResumeSubmissionStatus(item.submission_status),
    sourceAvailable: item.source_available,
    parsedText: item.parsed_text,
    parseMetadata: item.parse_result,
    errorMessage: item.error_message,
  };
}

export async function retryResumeSubmission(
  token: string,
  submissionId: string,
): Promise<ResumeDocumentUploadResult> {
  const result = await requestJson<RawUploadResult>(
    token,
    `/candidate-intakes/${submissionId}/retry`,
    { method: "POST", headers: { "Idempotency-Key": crypto.randomUUID() } },
  );
  return {
    documentId: result.document_id,
    submissionId: result.submission_id,
    workflowRunId: result.workflow_run_id,
    status: asResumeSubmissionStatus(result.status),
    reused: result.reused,
    candidateId: result.candidate_id,
    applicationId: result.application_id,
    applicationIds: result.application_ids ?? [],
  };
}

export function rebuildCandidateFromResume(
  token: string,
  submissionId: string,
) {
  return requestJson<{
    submission_id: string;
    workflow_run_id: string;
    status: ResumeSubmissionStatus;
    mode: "reparse" | "replacement";
  }>(token, `/candidate-intakes/${submissionId}/rebuild`, { method: "POST", headers: { "Idempotency-Key": crypto.randomUUID() } });
}

export function replaceCandidateResume(
  token: string,
  submissionId: string,
  file: File,
) {
  const body = new FormData();
  body.append("file", file);
  return requestJson<{
    submission_id: string;
    workflow_run_id: string;
    status: ResumeSubmissionStatus;
    mode: "reparse" | "replacement";
  }>(token, `/candidate-intakes/${submissionId}/replacement`, {
    method: "POST",
    body,
  });
}
