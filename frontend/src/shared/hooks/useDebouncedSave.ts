import { useCallback, useEffect, useRef, useState } from "react";
import { apiErrorMessage } from "../api/httpClient";

export type DraftSaveStatus = "idle" | "dirty" | "saving" | "saved" | "failed";

export interface DebouncedSaveController<T> {
  status: DraftSaveStatus;
  error: string;
  schedule: (value: T) => void;
  flush: () => Promise<boolean>;
  cancel: () => void;
}

export function useDebouncedSave<T>(
  save: (value: T) => Promise<unknown>,
  delayMs = 800
): DebouncedSaveController<T> {
  const [status, setStatus] = useState<DraftSaveStatus>("idle");
  const [error, setError] = useState("");
  const pendingRef = useRef<T>();
  const timerRef = useRef<number>();
  const savingRef = useRef<Promise<boolean>>();

  const cancel = useCallback(() => {
    if (timerRef.current !== undefined) window.clearTimeout(timerRef.current);
    timerRef.current = undefined;
    pendingRef.current = undefined;
    setStatus("idle");
    setError("");
  }, []);

  const flush = useCallback(async (): Promise<boolean> => {
    if (savingRef.current) return savingRef.current;
    const value = pendingRef.current;
    if (value === undefined) return true;
    pendingRef.current = undefined;
    if (timerRef.current !== undefined) window.clearTimeout(timerRef.current);
    timerRef.current = undefined;
    setStatus("saving");
    const operation = save(value)
      .then(() => {
        setStatus(pendingRef.current === undefined ? "saved" : "dirty");
        setError("");
        return true;
      })
      .catch((reason) => {
        pendingRef.current = value;
        setStatus("failed");
        setError(apiErrorMessage(reason, "草稿保存失败"));
        return false;
      })
      .finally(() => {
        savingRef.current = undefined;
      });
    savingRef.current = operation;
    return operation;
  }, [save]);

  const schedule = useCallback((value: T) => {
    pendingRef.current = value;
    setStatus("dirty");
    setError("");
    if (timerRef.current !== undefined) window.clearTimeout(timerRef.current);
    timerRef.current = window.setTimeout(() => void flush(), delayMs);
  }, [delayMs, flush]);

  useEffect(() => cancel, [cancel]);

  return { status, error, schedule, flush, cancel };
}
