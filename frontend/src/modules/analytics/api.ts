import { requestJson } from "@/shared/api/httpClient";

export interface AnalyticsSummary {
  departmentCount: number;
  candidateCount: number;
  finalReviewCount: number;
}

export interface DepartmentAnalyticsItem {
  department: string;
  total: number;
  finalReview: number;
  passed: number;
  rejected: number;
}

export interface StageAnalyticsItem {
  status: string;
  label: string;
  count: number;
}

export interface AnalyticsReadModel {
  summary: AnalyticsSummary;
  departments: DepartmentAnalyticsItem[];
  stages: StageAnalyticsItem[];
  permissions: string[];
}

export function getAnalyticsOverview(token: string): Promise<AnalyticsReadModel> {
  return requestJson(token, "/analytics/overview");
}