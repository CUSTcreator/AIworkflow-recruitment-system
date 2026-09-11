import type { ApplicationStatus } from "@/modules/applications/contracts";
import { requestJson } from "@/shared/api/httpClient";

export interface TaskListItem {
  taskId: string;
  applicationId: string;
  mainRoute: string;
  taskType: string;
  title: string;
  taskStatus: string;
  candidateName: string;
  school: string;
  highestDegree: string;
  jobTitle: string;
  jobMajorRequirement?: string;
  applicationStatus: ApplicationStatus;
  dueAt: string;
  overdue: boolean;
  currentScore?: number;
  assessmentUpdateStatus?: "idle" | "queued" | "running" | "review_required" | "failed" | "completed";
  recoveryActions?: Array<{ action: string; label: string }>;
  workbenchAvailable?: boolean;
}

export interface TaskListView {
  items: TaskListItem[];
  total: number;
  overdueCount: number;
  page: number;
  pageSize: number;
}

export interface TaskListQuery {
  page?: number;
  pageSize?: number;
  keyword?: string;
  jobId?: string;
  sortBy?: "priority" | "dueAt" | "currentScore";
  sortOrder?: "asc" | "desc";
}

export async function getMyTasks(
  token: string,
  query: TaskListQuery = {}
): Promise<TaskListView> {
  const params = new URLSearchParams();
  params.set("page", String(query.page ?? 1));
  params.set("pageSize", String(query.pageSize ?? 30));
  if (query.keyword?.trim()) params.set("keyword", query.keyword.trim());
  if (query.jobId) params.set("jobId", query.jobId);
  if (query.sortBy) params.set("sortBy", query.sortBy);
  if (query.sortOrder) params.set("sortOrder", query.sortOrder);
  return requestJson<TaskListView>(token, `/tasks/my?${params.toString()}`);
}
