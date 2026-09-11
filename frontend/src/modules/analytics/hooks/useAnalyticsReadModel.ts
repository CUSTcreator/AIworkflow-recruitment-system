import { useCallback } from "react";
import { getAnalyticsOverview, type AnalyticsReadModel } from "@/modules/analytics/api";
import { useAuth } from "@/modules/auth/AuthProvider";
import { useAsyncResource } from "@/shared/hooks/useAsyncResource";

export function useAnalyticsReadModel() {
  const { token } = useAuth();
  const load = useCallback(() => {
    if (!token) return Promise.reject(new Error("登录状态已失效"));
    return getAnalyticsOverview(token);
  }, [token]);

  return useAsyncResource<AnalyticsReadModel>(load, [token], { enabled: Boolean(token) });
}