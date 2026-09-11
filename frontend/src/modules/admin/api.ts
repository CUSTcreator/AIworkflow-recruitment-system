import { requestJson } from "@/shared/api/httpClient";
import type {
  BusinessPermission,
  PermissionEffect,
  ResponsibilityBundleCode
} from "@/modules/auth/permissions";

/** 后端权限目录投影；用于管理端展示，不在浏览器重复维护职责包组成。 */
export interface ResponsibilityBundleView {
  code: ResponsibilityBundleCode;
  label: string;
  description: string;
  requiredBusinessScope?: "organization";
  /** 混合范围职责中仅组织范围可用的子操作说明。 */
  organizationOnlyOperations?: string[];
}


export interface AdminUserView {
  userId: string;
  username: string;
  displayName: string;
  role: string;
  roleId: string;
  roleName: string;
  departmentId?: string;
  departmentName?: string;
  businessScope: "department" | "organization";
  /** 旧客户端原子权限覆盖的兼容读模型，新页面不直接编辑。 */
  permissionOverrides: Partial<Record<BusinessPermission, PermissionEffect>>;
  responsibilityOverrides: Partial<Record<ResponsibilityBundleCode, PermissionEffect>>;
  roleDefaultPermissions: BusinessPermission[];
  roleResponsibilityBundles: ResponsibilityBundleCode[];
  effectivePermissions: BusinessPermission[];
  effectiveResponsibilityBundles: ResponsibilityBundleCode[];
  responsibilityCatalog: ResponsibilityBundleView[];
  isSystemAdmin: boolean;
  isActive: boolean;
  deletedAt?: string;
  deletedByName?: string;
  mustChangePassword: boolean;
  lastLoginAt?: string;
}

export interface AdminRoleView {
  roleId: string;
  name: string;
  businessScope: "department" | "organization";
  /** 旧角色数据的原子权限读模型，仅供兼容和接口调试使用。 */
  permissions: BusinessPermission[];
  responsibilityBundles: ResponsibilityBundleCode[];
  responsibilityCatalog: ResponsibilityBundleView[];
  isSystem: boolean;
  isActive: boolean;
  userCount: number;
}

export interface AdminRoleWrite {
  name: string;
  businessScope: "department" | "organization";
  responsibilityBundles: ResponsibilityBundleCode[];
  isActive?: boolean;
}

export interface AdminDepartmentView {
  departmentId: string;
  name: string;
  managerCount: number;
  recruiterCount: number;
  activeUserCount: number;
  openJobCount: number;
  activeApplicationCount: number;
  deletedAt?: string;
  deletedByName?: string;
  lifecycleStatus: "normal" | "deleted";
}

export interface AdminJobView {
  jobId: string;
  title: string;
  departmentId: string;
  departmentName: string;
  status: "setup_pending" | "open" | "closed";
  headcount?: number;
  hiringManagerId?: string;
  hiringManagerName?: string;
  departmentRecruiterId?: string;
  departmentRecruiterName?: string;
  applicationCount: number;
  activeApplicationCount: number;
  deletedAt?: string;
  deletedByName?: string;
  lifecycleStatus: "setup_pending" | "open" | "closed" | "deleted";
}

export interface AdminWorkflowView {
  workflowRunId: string;
  workflowType: string;
  applicationId?: string;
  subjectType?: string;
  subjectId?: string;
  status: "pending" | "running" | "blocked" | "completed" | "failed" | "cancelled";
  attemptCount: number;
  maxAttempts: number;
  startedAt?: string;
  completedAt?: string;
  updatedAt?: string;
  errorMessage: string;
  errorCode?: string;
  availableActions: Array<"retry" | "resume">;
  currentStepName?: string;
  currentStepStatus?: string;
  nextAttemptAt?: string;
  /** 列表的最新安全事件摘要；完整事件仍按任务在运行轨迹弹窗中查看。 */
  latestEventMessage?: string;
  latestEventSeverity?: "info" | "warning" | "error";
  latestEventAt?: string;
}

/** 后端采用游标分页，避免系统管理页一次性装载全部历史记录。 */
export interface AdminPage<T> {
  items: T[];
  nextCursor?: string;
}

/** 任务轨迹只含可安全展示的步骤状态，不包含原始异常和外部回包。 */
export interface WorkflowExecutionEventView {
  executionEventId: string;
  workflowRunId: string;
  occurredAt: string;
  stepName?: string;
  stepLabel?: string;
  eventType: string;
  severity: "info" | "warning" | "error";
  message: string;
  attemptCount?: number;
  pollCount?: number;
  nextAttemptAt?: string;
  errorCategory?: string;
  errorCode?: string;
}

export interface DeletedApplicationView {
  applicationId: string;
  candidateName: string;
  jobTitle: string;
  departmentId: string;
  deletedAt?: string;
  deletedBy: string;
}

export interface WorkflowSummary {
  pending: number;
  running: number;
  blocked: number;
  completed: number;
  failed: number;
}

export interface ObjectCleanupTaskView {
  cleanupTaskId: string;
  applicationId: string;
  status: "pending" | "failed";
  pendingObjectCount: number;
  lastAttemptAt?: string;
  message: string;
}

export interface AuditEventView {
  auditEventId: string;
  actorUserId?: string;
  actorName: string;
  action: string;
  targetType: string;
  targetId: string;
  summary: string;
  details: Record<string, unknown>;
  createdAt?: string;
}

export interface AdminUserWrite {
  username: string;
  displayName: string;
  roleId: string;
  departmentId?: string;
  businessScope: "department" | "organization";
  responsibilityOverrides: Partial<Record<ResponsibilityBundleCode, PermissionEffect>>;
  isSystemAdmin: boolean;
  password?: string;
  isActive?: boolean;
}

export function getAdminRoles(token: string) {
  return requestJson<AdminRoleView[]>(token, "/admin/roles");
}

export function createAdminRole(token: string, input: AdminRoleWrite) {
  return requestJson<AdminRoleView>(token, "/admin/roles", {
    method: "POST",
    body: JSON.stringify(input)
  });
}

export function updateAdminRole(token: string, roleId: string, input: Partial<AdminRoleWrite>) {
  return requestJson<AdminRoleView>(token, `/admin/roles/${roleId}`, {
    method: "PATCH",
    headers: { "Idempotency-Key": crypto.randomUUID() },
    body: JSON.stringify(input)
  });
}

export function deleteAdminRole(token: string, roleId: string) {
  return requestJson<{ roleId: string; deleted: boolean }>(token, `/admin/roles/${roleId}`, {
    method: "DELETE"
  });
}

export function getAdminUsers(token: string, keyword = "") {
  const params = new URLSearchParams({ keyword });
  return requestJson<AdminUserView[]>(token, `/admin/users?${params.toString()}`);
}

export function createAdminUser(token: string, input: AdminUserWrite) {
  return requestJson<AdminUserView>(token, "/admin/users", {
    method: "POST",
    body: JSON.stringify(input)
  });
}

export function updateAdminUser(
  token: string,
  userId: string,
  input: Partial<Omit<AdminUserWrite, "password">>
) {
  return requestJson<AdminUserView>(token, `/admin/users/${userId}`, {
    method: "PATCH",
    headers: { "Idempotency-Key": crypto.randomUUID() },
    body: JSON.stringify(input)
  });
}

export function deleteAdminUser(token: string, userId: string) {
  return requestJson<{ userId: string; deleted: boolean; deletedAt: string }>(token, `/admin/users/${userId}`, {
    method: "DELETE"
  });
}

export function resetAdminUserPassword(token: string, userId: string, password: string) {
  return requestJson(token, `/admin/users/${userId}/reset-password`, {
    method: "POST",
    body: JSON.stringify({ password })
  });
}

export function getAdminDepartments(token: string) {
  return requestJson<AdminDepartmentView[]>(token, "/admin/departments");
}

export function createAdminDepartment(token: string, name: string) {
  return requestJson<AdminDepartmentView>(token, "/admin/departments", {
    method: "POST",
    body: JSON.stringify({ name })
  });
}

export function updateAdminDepartment(
  token: string,
  departmentId: string,
  input: { name?: string }
) {
  return requestJson<AdminDepartmentView>(token, `/admin/departments/${departmentId}`, {
    method: "PATCH",
    headers: { "Idempotency-Key": crypto.randomUUID() },
    body: JSON.stringify(input)
  });
}

export function deleteAdminDepartment(token: string, departmentId: string) {
  return requestJson<{ departmentId: string; deleted: boolean; deletedAt: string; deletedJobCount: number }>(
    token,
    `/admin/departments/${departmentId}`,
    { method: "DELETE", headers: { "Idempotency-Key": crypto.randomUUID() } }
  );
}

export function getAdminJobs(token: string) {
  return requestJson<AdminJobView[]>(token, "/admin/jobs");
}

export function updateAdminJob(
  token: string,
  jobId: string,
  input: { status?: "setup_pending" | "open" | "closed"; headcount?: number; hiringManagerId?: string; departmentRecruiterId?: string }
) {
  return requestJson<AdminJobView>(token, `/admin/jobs/${jobId}`, {
    method: "PATCH",
    headers: { "Idempotency-Key": crypto.randomUUID() },
    body: JSON.stringify(input)
  });
}

export function deleteAdminJob(token: string, jobId: string) {
  return requestJson<{ jobId: string; deleted: boolean; deletedAt: string }>(
    token,
    `/admin/jobs/${jobId}`,
    { method: "DELETE", headers: { "Idempotency-Key": crypto.randomUUID() } }
  );
}

export async function getAdminWorkflows(token: string, options: { status?: string; cursor?: string; limit?: number } = {}) {
  const params = new URLSearchParams({ limit: String(options.limit ?? 50) });
  if (options.status) params.set("status", options.status);
  if (options.cursor) params.set("cursor", options.cursor);
  const [summary, page] = await Promise.all([
    requestJson<WorkflowSummary>(token, "/admin/workflow-runs/summary"),
    requestJson<AdminPage<AdminWorkflowView>>(token, `/admin/workflow-runs?${params.toString()}`)
  ]);
  return { summary, ...page };
}

export function getAdminWorkflowExecutionEvents(
  token: string,
  workflowRunId: string,
  options: { cursor?: string; limit?: number } = {}
) {
  const params = new URLSearchParams({ limit: String(options.limit ?? 50) });
  if (options.cursor) params.set("cursor", options.cursor);
  return requestJson<AdminPage<WorkflowExecutionEventView>>(
    token,
    `/admin/workflow-runs/${workflowRunId}/execution-events?${params.toString()}`
  );
}

export function retryAdminWorkflow(token: string, workflowRunId: string, reason: string) {
  return requestJson(token, `/admin/workflow-runs/${workflowRunId}/retry`, {
    method: "POST",
    body: JSON.stringify({ reason })
  });
}

export interface AdminAuditEventQuery {
  action?: string;
  category?: "account" | "organization" | "recruitment" | "task";
  query?: string;
  actorUserId?: string;
  from?: string;
  to?: string;
  cursor?: string;
  limit?: number;
}

export function getAdminAuditEvents(token: string, options: AdminAuditEventQuery = {}) {
  const params = new URLSearchParams({ limit: String(options.limit ?? 50) });
  if (options.action) params.set("action", options.action);
  if (options.category) params.set("category", options.category);
  if (options.query) params.set("query", options.query);
  if (options.actorUserId) params.set("actorUserId", options.actorUserId);
  if (options.from) params.set("from", options.from);
  if (options.to) params.set("to", options.to);
  if (options.cursor) params.set("cursor", options.cursor);
  return requestJson<AdminPage<AuditEventView>>(token, `/admin/audit-events?${params.toString()}`);
}

export function getDeletedApplications(token: string, cursor = "") {
  const params = new URLSearchParams({ limit: "50" });
  if (cursor) params.set("cursor", cursor);
  return requestJson<AdminPage<DeletedApplicationView>>(token, `/admin/deleted-applications?${params.toString()}`);
}

export function permanentlyDeleteApplication(token: string, applicationId: string) {
  return requestJson<{
    applicationId: string;
    permanentlyDeleted: boolean;
    cleanupWarnings: string[];
    cleanupTaskId?: string;
  }>(token, `/admin/applications/${applicationId}`, {
    method: "DELETE"
  });
}

export function getObjectCleanupTasks(token: string) {
  return requestJson<ObjectCleanupTaskView[]>(token, "/admin/object-cleanup-tasks");
}

export function retryObjectCleanup(token: string, cleanupTaskId: string) {
  return requestJson<{ cleanupTaskId: string; status: "pending" }>(
    token,
    `/admin/object-cleanup-tasks/${cleanupTaskId}/retry`,
    { method: "POST", headers: { "Idempotency-Key": crypto.randomUUID() } }
  );
}

export interface HardScreeningCatalogCriterionWrite {
  name: string;
  code?: string;
  valueMode: "select" | "number" | "text";
  allowedValues: string[];
  evaluationBinding: string;
  enabled: boolean;
  sortOrder: number;
}

export interface AdminHardScreeningCatalogCriterion extends HardScreeningCatalogCriterionWrite {
  criterionId: string;
  isBuiltin: boolean;
  deletedAt?: string | null;
}

export function getAdminHardScreeningCriteria(token: string) {
  return requestJson<AdminHardScreeningCatalogCriterion[]>(token, "/admin/hard-screening-criteria");
}
export function createAdminHardScreeningCriterion(token: string, body: HardScreeningCatalogCriterionWrite) {
  return requestJson<AdminHardScreeningCatalogCriterion>(token, "/admin/hard-screening-criteria", { method: "POST", body: JSON.stringify(body) });
}
export function updateAdminHardScreeningCriterion(token: string, criterionId: string, body: Partial<HardScreeningCatalogCriterionWrite>) {
  return requestJson<AdminHardScreeningCatalogCriterion>(token, `/admin/hard-screening-criteria/${criterionId}`, { method: "PATCH", body: JSON.stringify(body) });
}
export function deleteAdminHardScreeningCriterion(token: string, criterionId: string) {
  return requestJson<{ criterionId: string; deleted: boolean }>(token, `/admin/hard-screening-criteria/${criterionId}`, { method: "DELETE", headers: { "Idempotency-Key": crypto.randomUUID() } });
}
export function resetAdminHardScreeningCriteria(token: string) {
  return requestJson<AdminHardScreeningCatalogCriterion[]>(token, "/admin/hard-screening-criteria/reset", { method: "POST" });
}
