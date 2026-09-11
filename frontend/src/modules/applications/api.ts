import type { ApplicationStatus } from "./contracts";
import type {
  WorkflowExecutionEvent,
  WorkflowProcess,
} from "@/shared/workflows/process";
import type { GateStatus } from "@/modules/assessment/contracts";
import { requestJson, toAbsoluteApiUrl } from "@/shared/api/httpClient";
import type { RecoveryAction } from "@/shared/recovery/actions";
import type { JobAssignmentRole } from "@/modules/jobs/jobConfiguration";

export interface WorkflowExecutionTimelineResponse {
  items: WorkflowExecutionEvent[];
}

export interface AssessmentStageViewAction {
  action: string;
  label: string;
  route: string;
}

export interface AssessmentStageSummary {
  stage: "v1" | "v2" | "v3";
  label: string;
  status: "not_started" | "processing" | "recoverable" | "completed";
  resultAvailable: boolean;
  publishedAssessmentVersionId?: string | null;
  viewAction?: AssessmentStageViewAction | null;
}

export interface CurrentExecution {
  stage: string;
  label: string;
  process: WorkflowProcess;
}

export function getApplicationWorkflowTimeline(
  token: string,
  applicationId: string,
  workflowRunId: string,
): Promise<WorkflowExecutionTimelineResponse> {
  const query = new URLSearchParams({ workflowRunId });
  return requestJson(
    token,
    `/applications/${applicationId}/workflow-timeline?${query}`,
  );
}

export interface RecruitmentTimelineItem {
  eventId: string;
  action: string;
  kind: string;
  label: string;
  fromStatus: string;
  toStatus: string;
  note: string;
  actorName: string;
  effectiveAt: string;
  timezone: string;
  recordedAt: string;
}

export function getRecruitmentTimeline(
  token: string,
  applicationId: string,
): Promise<{ applicationId: string; items: RecruitmentTimelineItem[] }> {
  return requestJson(token, `/applications/${applicationId}/timeline`);
}

export type HardScreeningScope =
  | "full_resume"
  | "education"
  | "skills"
  | "work_experience"
  | "project_experience"
  | "certifications"
  | "awards";

export type HardScreeningOperator =
  | "exists"
  | "contains_any"
  | "contains_all"
  | "not_contains_any"
  | "degree_at_least"
  | "education_status_is"
  | "year_between"
  | "years_at_least"
  | "years_between"
  | "semantic_match";

export type HardScreeningCriterionType =
  | "minimum_degree"
  | "highest_education_status"
  | "highest_education_graduation_year"
  | "minimum_experience_years"
  | "project_experience"
  | "required_skill"
  | "certification"
  | "custom";

export interface HardScreeningCondition {
  selectedValues?: string[];
  min?: number | null;
  max?: number | null;
  text?: string;
}

export interface HardScreeningCatalogCriterion {
  criterionId: string;
  code: string;
  name: string;
  valueMode: "select" | "number" | "text";
  allowedValues: string[];
  evaluationBinding: string;
  enabled: boolean;
  sortOrder: number;
  isBuiltin: boolean;
}

export interface HardScreeningRuleInput {
  ruleId?: string;
  criterionType?: HardScreeningCriterionType;
  criterionId?: string;
  name: string;
  valueMode?: "select" | "number" | "text";
  evaluationBinding?: string;
  condition?: HardScreeningCondition;
  sourceScope?: HardScreeningScope;
  operator?: HardScreeningOperator;
  // years_between is persisted by the backend as a closed interval.
  expectedValue?: string | number | string[] | { min?: number; max?: number };
  description?: string;
  enabled?: boolean;
}
export interface HardScreeningResult {
  applicationId: string;
  resultId?: string;
  policyId?: string;
  status:
    | "not_configured"
    | "pending"
    | "running"
    | "passed"
    | "failed"
    | "manual_review";
  summary: string;
  ruleResults: Array<{
    rule_id: string;
    name: string;
    /** 后端根据本次冻结规则生成，前端不得自行解释 operator。 */
    requirementText: string;
    status: "passed" | "failed" | "manual_review";
    reason: string;
    source_quotes: string[];
  }>;
}

export type HardScreeningFilter =
  | "not_configured"
  | "pending"
  | "running"
  | "processing"
  | "passed"
  | "failed"
  | "manual_review";

export interface HardScreeningPreview {
  totalCount: number;
  passedCount: number;
  failedCount: number;
  reviewCount: number;
  reasons: Array<{
    ruleName: string;
    reason: string;
  }>;
}

export interface DepartmentOption {
  departmentId: string;
  name: string;
}

export interface PreScreeningProcess {
  status:
    | "waiting_job_profile"
    | "job_profile_queued"
    | "job_profile_processing"
    | "job_profile_review_required"
    | "job_profile_failed"
    | "initial_assessment_failed"
    | "hard_screening_queued"
    | "hard_screening_running"
    | "hard_screening_failed"
    | "hard_screening_review_required"
    | "v1_scheduling"
    | "v1_queued"
    | "v1_running"
    | "v1_failed";
  label: string;
  message: string;
  tone: "neutral" | "info" | "warning" | "danger";
  workflowRunId?: string;
}

export interface ApplicationListItem {
  applicationId: string;
  jobId: string;
  mainRoute: string;
  workspaceAction?: {
    action: string;
    label: string;
    route: string;
  } | null;
  resumeSubmissionId?: string;
  resumeFilename?: string;
  documentStatus: string;
  candidateResolved: boolean;
  processingStage: string;
  processingError: string;
  canRetry: boolean;
  candidateName: string;
  anonymizedCode: string;
  currentTitle: string;
  age?: number;
  yearsOfExperience: string;
  school: string;
  major: string;
  highestDegree: string;
  resumePdfUrl?: string;
  jobTitle: string;
  jobMajorRequirement?: string;
  department: string;
  jobConfigurationStatus: "complete" | "incomplete";
  missingJobAssignments: JobAssignmentRole[];
  canConfigureJob: boolean;
  status: ApplicationStatus;
  resumeRebuildStatus:
    "idle" | "processing" | "review_required" | "completed" | "failed";
  resumeRebuildMessage: string;
  resumeRebuildProcess?: WorkflowProcess;
  // 由后端聚合底层状态，前端不能再组合 Application/Job/Workflow 状态猜当前阶段。
  preScreeningProcess?: PreScreeningProcess;
  rejectionStage?:
    | "hard_screening"
    | "screening"
    | "first_interview"
    | "hr_review"
    | "second_interview"
    | "final_review";
  hardScreeningStatus: HardScreeningResult["status"];
  hardScreeningSummary: string;
  hardScreeningPreview: HardScreeningPreview;
  hardScreeningProcess?: WorkflowProcess;
  dueAt: string;
  overdue: boolean;
  scoreStatus: "scored" | "failed" | "pending";
  screeningError: string;
  // 初筛 V1 的独立任务，不能与候选人级简历重建混用。
  screeningProcess?: WorkflowProcess;
  baseScore?: number;
  currentScore?: number;
  scoreStage?: string;
  assessmentUpdateStatus:
    "idle" | "queued" | "running" | "review_required" | "failed" | "completed";
  assessmentUpdateStage?: "first" | "second";
  assessmentUpdateMessage: string;
  assessmentUpdateProcess?: WorkflowProcess;
  canRetryAssessmentUpdate: boolean;
  availableActions: string[];
  recoveryActions: RecoveryAction[];
  assessmentStages?: AssessmentStageSummary[];
  currentExecution?: CurrentExecution | null;
  qualificationGate: GateStatus;
  submittedAt: string;
  updatedAt: string;
}

export interface HardScreeningPolicyView {
  jobId: string;
  policyId?: string;
  version: number;
  enabled: boolean;
  rules: HardScreeningRuleInput[];
  generationMode?: "job_requirement_auto" | "user_confirmed_auto" | "user_managed";
  generationStatus?: "pending_confirmation" | "degraded" | "confirmed" | "disabled";
  sourceJdVersionId?: string;
}

export interface ApplicationListView {
  items: ApplicationListItem[];
  page: number;
  pageSize: number;
  total: number;
  groupCounts: Record<"in_progress" | "passed" | "rejected" | "cancelled", number>;
}

export interface ApplicationFilterJobOption {
  jobId: string;
  title: string;
  departmentId: string;
  departmentName: string;
}

export interface ApplicationFilterDepartmentOption {
  departmentId: string;
  name: string;
}

export interface ApplicationFilterOptionsView {
  departments: ApplicationFilterDepartmentOption[];
  jobs: ApplicationFilterJobOption[];
}

export interface ApplicationListQuery {
  page?: number;
  pageSize?: number;
  status?: string;
  group?: "in_progress" | "passed" | "rejected" | "cancelled";
  jobId?: string;
  departmentId?: string;
  hardScreeningStatus?: HardScreeningFilter;
  highestDegree?: string;
  majorKeyword?: string;
  minimumScore?: number;
  overdueOnly?: boolean;
  attentionOnly?: boolean;
  submittedFrom?: string;
  submittedTo?: string;
  sortBy?: "submittedAt" | "currentScore" | "updatedAt";
  sortOrder?: "asc" | "desc";
  keyword?: string;
}

export async function getApplicationList(
  token: string,
  query: ApplicationListQuery = {},
): Promise<ApplicationListView> {
  const params = new URLSearchParams();
  params.set("page", String(query.page ?? 1));
  params.set("pageSize", String(query.pageSize ?? 20));
  if (query.status) params.set("status", query.status);
  if (query.group) params.set("group", query.group);
  if (query.jobId) params.set("jobId", query.jobId);
  if (query.departmentId) params.set("departmentId", query.departmentId);
  if (query.hardScreeningStatus) {
    params.set("hardScreeningStatus", query.hardScreeningStatus);
  }
  if (query.highestDegree) params.set("highestDegree", query.highestDegree);
  if (query.majorKeyword?.trim()) params.set("majorKeyword", query.majorKeyword.trim());
  if (query.minimumScore !== undefined) {
    params.set("minimumScore", String(query.minimumScore));
  }
  if (query.overdueOnly) params.set("overdueOnly", "true");
  if (query.attentionOnly) params.set("attentionOnly", "true");
  if (query.submittedFrom) params.set("submittedFrom", query.submittedFrom);
  if (query.submittedTo) params.set("submittedTo", query.submittedTo);
  if (query.sortBy) params.set("sortBy", query.sortBy);
  if (query.sortOrder) params.set("sortOrder", query.sortOrder);
  if (query.keyword?.trim()) params.set("keyword", query.keyword.trim());
  const view = await requestJson<ApplicationListView>(
    token,
    `/applications?${params.toString()}`,
  );
  return {
    ...view,
    items: view.items.map((item) => ({
      ...item,
      resumePdfUrl: toAbsoluteApiUrl(item.resumePdfUrl),
    })),
  };
}

export function getApplicationFilterOptions(
  token: string,
): Promise<ApplicationFilterOptionsView> {
  return requestJson(token, "/applications/filter-options");
}

export async function getAllApplications(
  token: string,
): Promise<ApplicationListItem[]> {
  const first = await getApplicationList(token, { page: 1, pageSize: 100 });
  const pageCount = Math.ceil(first.total / first.pageSize);
  if (pageCount <= 1) return first.items;
  const remaining = await Promise.all(
    Array.from({ length: pageCount - 1 }, (_, index) =>
      getApplicationList(token, { page: index + 2, pageSize: first.pageSize }),
    ),
  );
  return [first, ...remaining].flatMap((page) => page.items);
}

export function deleteApplication(token: string, applicationId: string) {
  return requestJson<{
    applicationId: string;
    deleted: boolean;
    deletedAt: string;
  }>(token, `/applications/${applicationId}`, {
    method: "DELETE",
  });
}

export function getDepartments(token: string): Promise<DepartmentOption[]> {
  return requestJson<DepartmentOption[]>(token, "/departments");
}

export function getHardScreeningResult(
  token: string,
  applicationId: string,
): Promise<HardScreeningResult> {
  return requestJson(
    token,
    `/applications/${applicationId}/hard-screening-result`,
  );
}

export function getAvailableHardScreeningCriteria(
  token: string,
): Promise<HardScreeningCatalogCriterion[]> {
  return requestJson(token, "/hard-screening-criteria");
}
export function getHardScreeningPolicy(
  token: string,
  jobId: string,
): Promise<HardScreeningPolicyView> {
  return requestJson<
    HardScreeningPolicyView & {
      rules: Array<
        HardScreeningRuleInput & {
          rule_id?: string;
          criterion_type?: HardScreeningCriterionType;
          source_scope?: HardScreeningScope;
          expected_value?: string | number | string[];
        }
      >;
    }
  >(token, `/jobs/${jobId}/hard-screening-policy`).then((policy) => ({
    ...policy,
    rules: policy.rules.map((rule) => {
      const raw = rule as HardScreeningRuleInput & {
        rule_id?: string;
        criterion_type?: HardScreeningCriterionType;
        criterion_id?: string;
        value_mode?: "select" | "number" | "text";
        evaluation_binding?: string;
        source_scope?: HardScreeningScope;
        expected_value?: string | number | string[];
      };
      // 后端返回的策略项可能包含 schema_version 等持久化元数据；
      // 保存接口采用 extra=forbid，只发送公开 DTO 允许的字段，避免把内部字段回传导致 422。
      return {
        name: raw.name,
        ruleId: raw.ruleId ?? raw.rule_id,
        criterionType: raw.criterionType ?? raw.criterion_type,
        criterionId: raw.criterionId ?? raw.criterion_id,
        valueMode: raw.valueMode ?? raw.value_mode,
        evaluationBinding: raw.evaluationBinding ?? raw.evaluation_binding,
        condition: raw.condition
          ? (() => {
              const condition = raw.condition as HardScreeningCondition & {
                selected_values?: string[];
              };
              // 同时兼容历史 snake_case 读取结果，但保存时只保留公开字段，
              // 避免 selected_values 与 selectedValues 一起触发 extra=forbid。
              return {
                selectedValues: condition.selectedValues ?? condition.selected_values,
                min: condition.min,
                max: condition.max,
                text: condition.text,
              };
            })()
          : undefined,
        sourceScope: raw.sourceScope ?? raw.source_scope,
        expectedValue: raw.expectedValue ?? raw.expected_value,
        operator: raw.operator,
        description: raw.description,
        enabled: raw.enabled ?? true,
      };
    }),
  }));
}

export async function saveHardScreeningPolicy(
  token: string,
  jobId: string,
  policy: Pick<HardScreeningPolicyView, "enabled" | "rules">,
): Promise<HardScreeningPolicyView> {
  await requestJson(token, `/jobs/${jobId}/hard-screening-policy`, {
    method: "PUT",
    headers: { "Idempotency-Key": crypto.randomUUID() },
    body: JSON.stringify(policy),
  });
  return getHardScreeningPolicy(token, jobId);
}

export function reviewHardScreening(
  token: string,
  applicationId: string,
  decision: "pass" | "reject",
  reason: string,
): Promise<{
  applicationId: string;
  status: string;
  hardScreeningStatus: string;
}> {
  return requestJson(
    token,
    `/applications/${applicationId}/hard-screening-review`,
    {
      method: "POST",
      headers: { "Idempotency-Key": crypto.randomUUID() },
      body: JSON.stringify({
        decision,
        reason,
      }),
    },
  );
}

export function retryHardScreening(
  token: string,
  applicationId: string,
): Promise<{ applicationId: string; status: string; workflowRunId: string }> {
  return requestJson(
    token,
    `/applications/${applicationId}/hard-screening-retry`,
    {
      method: "POST",
      headers: { "Idempotency-Key": crypto.randomUUID() },
    },
  );
}

export function retryInitialAssessment(
  token: string,
  applicationId: string,
): Promise<{
  application_id: string;
  status: string;
  message: string;
  workflow_run_id: string;
}> {
  return requestJson(
    token,
    `/applications/${applicationId}/initial-assessment/retry`,
    {
      method: "POST",
      headers: { "Idempotency-Key": crypto.randomUUID() },
    },
  );
}
