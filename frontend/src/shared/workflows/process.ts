/**
 * 后端 WorkflowProcessView 的唯一前端合同。
 *
 * 页面不再读取 WorkflowRun 原始状态、检查点或异常文本后自行推断业务文案；
 * 只根据本合同决定状态展示、轮询和后端授权的可用操作。
 */
export type WorkflowProcessStatus =
  | "not_started"
  | "queued"
  | "running"
  | "retry_wait"
  | "waiting_external"
  | "blocked"
  | "failed"
  | "completed"
  | "cancelled";

export type WorkflowRecoveryAction = "" | "auto_retry" | "user_retry" | "review_required" | "continue_manually";

export interface WorkflowProcess {
  workflowRunId: string;
  workflowType: string;
  processStatus: WorkflowProcessStatus;
  currentStep: string;
  currentStepLabel: string;
  attemptCount: number;
  maxAttempts: number;
  pollCount: number;
  maxPollAttempts: number;
  nextAttemptAt: string;
  publicMessage: string;
  recoveryAction: WorkflowRecoveryAction;
  recoveryActionLabel: string;
  updatedAt: string;
}

/** 业务详情展示的安全执行事件，不包含原始异常、外部回包或诊断 JSON。 */
export interface WorkflowExecutionEvent {
  executionEventId: string;
  workflowRunId: string;
  occurredAt: string;
  stepName: string;
  stepLabel: string;
  eventType: string;
  severity: "info" | "warning" | "error" | string;
  message: string;
  attemptCount?: number | null;
  pollCount?: number | null;
  nextAttemptAt?: string;
  errorCategory?: string;
  errorCode?: string;
  recoveryAction?: WorkflowRecoveryAction;
  recoveryActionLabel?: string;
  errorCodeLabel?: string;
}


export const activeWorkflowProcessStatuses = new Set<WorkflowProcessStatus>([
  "queued",
  "running",
  "retry_wait",
  "waiting_external"
]);

export function isWorkflowProcessActive(process?: Pick<WorkflowProcess, "processStatus"> | null): boolean {
  return Boolean(process && activeWorkflowProcessStatuses.has(process.processStatus));
}



