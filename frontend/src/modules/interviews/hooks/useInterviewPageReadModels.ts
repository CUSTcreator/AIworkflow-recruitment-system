import { useCallback } from "react";
import { useAuth } from "@/modules/auth/AuthProvider";
import {
  getFirstInterviewWorkspaceView,
  type FirstInterviewWorkspaceReadModel
} from "@/modules/interviews/firstInterviewApi";
import {
  getFinalReviewView,
  getSecondInterviewReviewView,
  getSecondInterviewWorkspaceView,
  type FinalReviewReadModel,
  type SecondInterviewReviewReadModel,
  type SecondInterviewWorkspaceReadModel
} from "@/modules/interviews/secondInterviewApi";
import { useAsyncResource } from "@/shared/hooks/useAsyncResource";

export function useFirstInterviewWorkspaceReadModel(applicationId: string) {
  const { token } = useAuth();
  const load = useCallback(() => {
    if (!token) return Promise.reject(new Error("登录状态已失效"));
    return getFirstInterviewWorkspaceView(token, applicationId);
  }, [applicationId, token]);

  return useAsyncResource<FirstInterviewWorkspaceReadModel>(
    load,
    [applicationId, token],
    { enabled: Boolean(token) }
  );
}

export function useSecondInterviewWorkspaceReadModel(applicationId: string) {
  const { token } = useAuth();
  const load = useCallback(() => {
    if (!token) return Promise.reject(new Error("登录状态已失效"));
    return getSecondInterviewWorkspaceView(token, applicationId);
  }, [applicationId, token]);

  return useAsyncResource<SecondInterviewWorkspaceReadModel>(
    load,
    [applicationId, token],
    { enabled: Boolean(token) }
  );
}

export function useSecondInterviewReviewReadModel(
  applicationId: string,
  mode: "review" | "final"
) {
  const { token } = useAuth();
  const load = useCallback(() => {
    if (!token) return Promise.reject(new Error("登录状态已失效"));
    return mode === "final"
      ? getFinalReviewView(token, applicationId)
      : getSecondInterviewReviewView(token, applicationId);
  }, [applicationId, mode, token]);

  const resource = useAsyncResource<SecondInterviewReviewReadModel | FinalReviewReadModel>(
    load,
    [applicationId, mode, token],
    { enabled: Boolean(token) }
  );

  return { ...resource, token };
}