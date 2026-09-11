import { useCallback, useEffect, useRef, useState, type DependencyList } from "react";
import { apiErrorMessage, isApiError } from "../api/httpClient";

export interface AsyncResourceState<T> {
  data: T | undefined;
  loading: boolean;
  error: string;
  /** 后端可识别业务错误码；页面可据此提供下一步操作，而非只显示“加载失败”。 */
  errorCode?: string;
  reload: () => Promise<T | undefined>;
}

export function useAsyncResource<T>(
  loader: () => Promise<T>,
  dependencies: DependencyList,
  options: { enabled?: boolean; initialData?: T } = {}
): AsyncResourceState<T> {
  const { enabled = true, initialData } = options;
  const [data, setData] = useState<T | undefined>(initialData);
  const [loading, setLoading] = useState(enabled && initialData === undefined);
  const [error, setError] = useState("");
  const [errorCode, setErrorCode] = useState<string>();
  const requestIdRef = useRef(0);

  const reload = useCallback(async () => {
    if (!enabled) return undefined;
    const requestId = ++requestIdRef.current;
    setLoading(true);
    try {
      const next = await loader();
      if (requestId === requestIdRef.current) {
        setData(next);
        setError("");
        setErrorCode(undefined);
      }
      return next;
    } catch (reason) {
      if (requestId === requestIdRef.current) {
        setError(apiErrorMessage(reason));
        setErrorCode(isApiError(reason) ? reason.code : undefined);
      }
      return undefined;
    } finally {
      if (requestId === requestIdRef.current) setLoading(false);
    }
  }, [enabled, loader]);

  useEffect(() => {
    void reload();
    return () => {
      requestIdRef.current += 1;
    };
  }, [reload, ...dependencies]);

  return { data, loading, error, errorCode, reload };
}
