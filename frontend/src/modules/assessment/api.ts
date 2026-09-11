import type { ApplicationStatus } from "@/modules/applications/contracts";
import type { EvidenceIndexItem, ScreeningReviewReadModel } from "./contracts";
import { requestJson } from "@/shared/api/httpClient";
import type { WorkflowProcess, WorkflowProcessStatus } from "@/shared/workflows/process";
import type { RecoveryAction } from "@/shared/recovery/actions";

export function getScreeningReviewView(token: string, applicationId: string): Promise<ScreeningReviewReadModel> {
  return requestJson(token, `/applications/${applicationId}/views/screening-review`);
}

export interface ScoringStatusView {
  applicationId: string;
  jobId?: string;
  resumeSubmissionId?: string;
  runStatus: WorkflowProcessStatus;
  applicationStatus: ApplicationStatus;
  stage: string;
  updatedAt: string;
  /** 普通页面只使用 process.publicMessage，不显示原始技术异常。 */
  process?: WorkflowProcess;
  error?: string;
  recoveryActions?: RecoveryAction[];
}

export function getScoringStatus(token: string, applicationId: string): Promise<ScoringStatusView> {
  return requestJson(token, `/applications/${applicationId}/workflows/scoring/status`);
}


export function getEvidenceDetail(token: string, applicationId: string, evidenceId: string): Promise<EvidenceIndexItem> {
  return requestJson(token, `/applications/${applicationId}/evidence/${evidenceId}`);
}
