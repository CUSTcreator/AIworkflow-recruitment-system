import { useCallback, useEffect, useRef, useState } from "react";
import { apiErrorMessage } from "../api/httpClient";
import { activeWorkflowProcessStatuses, type WorkflowProcessStatus } from "@/shared/workflows/process";

export type WorkflowRunStatus = WorkflowProcessStatus | "pending" | "ready";

export interface WorkflowStatusLike {
  runStatus?: WorkflowRunStatus;
  status?: WorkflowRunStatus;
  workflowRunId?: string;
  processStatus?: WorkflowProcessStatus;
  error?: string | null;
}

export interface WorkflowPollingState<T> {
  data: T | undefined;
  polling: boolean;
  error: string;
  refresh: () => Promise<T | undefined>;
}

const ACTIVE_STATUSES = new Set<WorkflowRunStatus>(["pending", ...activeWorkflowProcessStatuses]);

function statusOf(value: WorkflowStatusLike | undefined): WorkflowRunStatus {
  return value?.processStatus ?? value?.runStatus ?? value?.status ?? "not_started";
}

export function useWorkflowPolling<T extends WorkflowStatusLike>(
  load: () => Promise<T>,
  options: {
    enabled?: boolean;
    intervalMs?: number;
    onCompleted?: (status: T) => void | Promise<void>;
    onFailed?: (status: T) => void;
  } = {}
): WorkflowPollingState<T> {
  const { enabled = true, intervalMs = 1500, onCompleted, onFailed } = options;
  const [data, setData] = useState<T>();
  const [polling, setPolling] = useState(false);
  const [error, setError] = useState("");
  const timerRef = useRef<number>();
  const mountedRef = useRef(true);

  const refresh = useCallback(async () => {
    if (!enabled) return undefined;
    try {
      const next = await load();
      if (!mountedRef.current) return next;
      setData(next);
      setError("");
      const status = statusOf(next);
      setPolling(ACTIVE_STATUSES.has(status));
      if (status === "completed" || status === "ready") await onCompleted?.(next);
      if (status === "failed") onFailed?.(next);
      return next;
    } catch (reason) {
      if (mountedRef.current) {
        setPolling(false);
        setError(apiErrorMessage(reason, "任务状态加载失败"));
      }
      return undefined;
    }
  }, [enabled, load, onCompleted, onFailed]);

  useEffect(() => {
    mountedRef.current = true;
    void refresh();
    return () => {
      mountedRef.current = false;
      if (timerRef.current !== undefined) window.clearTimeout(timerRef.current);
    };
  }, [refresh]);

  useEffect(() => {
    if (!polling) return;
    timerRef.current = window.setTimeout(() => void refresh(), intervalMs);
    return () => {
      if (timerRef.current !== undefined) window.clearTimeout(timerRef.current);
    };
  }, [intervalMs, polling, refresh, data]);

  return { data, polling, error, refresh };
}
