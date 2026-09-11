import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useRequiredApplicationId } from "@/modules/applications/hooks/useRequiredApplicationId";
import { retryHardScreening as requestHardScreeningRetry } from "@/modules/applications/api";
import { useWorkflowActions } from "@/modules/recruitment_workflow/workflowActions";
import { useAuth } from "@/modules/auth/AuthProvider";
import { getEvidenceDetail, getScoringStatus, getScreeningReviewView } from "@/modules/assessment/api";
import type { EvidenceIndexItem, ScreeningReviewReadModel } from "@/modules/assessment/contracts";
import { useAsyncResource } from "@/shared/hooks/useAsyncResource";
import { useWorkflowPolling } from "@/shared/hooks/useWorkflowPolling";

export function useScreeningReviewController() {
  const applicationId = useRequiredApplicationId();
  const navigate = useNavigate();
  const { token } = useAuth();
  const { approveFirstInterview, makeDepartmentDecision, repairJobProfile, runScoring } = useWorkflowActions();
  const [activeEvidence, setActiveEvidence] = useState<EvidenceIndexItem>();
  const evidenceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const loadContext = useCallback(() => {
    if (!token) return Promise.reject(new Error("登录状态已失效"));
    return getScreeningReviewView(token, applicationId);
  }, [applicationId, token]);

  const resource = useAsyncResource<ScreeningReviewReadModel>(
    loadContext,
    [applicationId, token],
    { enabled: Boolean(token) }
  );

  // 初筛结果尚未生成时，主读模型会返回“结果未就绪”。状态接口独立轮询，
  // 任务完成后再刷新主读模型，避免页面长期停留在“加载失败”。
  const reloadScreeningView = useCallback(async () => {
    await resource.reload();
  }, [resource.reload]);

  const loadScoringStatus = useCallback(() => {
    if (!token) return Promise.reject(new Error("登录状态已失效"));
    return getScoringStatus(token, applicationId);
  }, [applicationId, token]);

  const scoringWorkflow = useWorkflowPolling(
    loadScoringStatus,
    {
      enabled: Boolean(token),
      intervalMs: 3000,
      onCompleted: reloadScreeningView,
    }
  );

  useEffect(() => () => {
    if (evidenceTimerRef.current) clearTimeout(evidenceTimerRef.current);
  }, []);

  const showEvidence = useCallback(async (evidenceId: string) => {
    const evidence = resource.data?.screeningResult.evidenceIndex[evidenceId];
    setActiveEvidence(evidence ?? { evidenceId });
    if (token) {
      try {
        setActiveEvidence(await getEvidenceDetail(token, applicationId, evidenceId));
      } catch {
        // Keep the summary visible when the detail request fails.
      }
    }
    if (evidenceTimerRef.current) clearTimeout(evidenceTimerRef.current);
    evidenceTimerRef.current = setTimeout(() => {
      setActiveEvidence(undefined);
      evidenceTimerRef.current = null;
    }, 5000);
  }, [applicationId, resource.data, token]);

  const go = useCallback(async (action: Promise<string | undefined>) => {
    const path = await action;
    if (path) navigate(path);
  }, [navigate]);

  return {
    applicationId,
    context: resource.data,
    loading: resource.loading,
    loadError: resource.error,
    reload: resource.reload,
    workflowStatus: scoringWorkflow.data ?? resource.data?.workflowStatus,
    recoveryActions: scoringWorkflow.data?.recoveryActions ?? resource.data?.recoveryActions ?? [],
    workflowPolling: scoringWorkflow.polling,
    workflowError: scoringWorkflow.error,
    activeEvidence,
    showEvidence,
    approve: () => go(approveFirstInterview(applicationId)),
    reject: () => go(makeDepartmentDecision(applicationId, "不推进")),
    retryScoring: async () => {
      await runScoring(applicationId);
      await scoringWorkflow.refresh();
      await resource.reload();
    },
    retryHardScreening: async () => {
      if (!token) return;
      await requestHardScreeningRetry(token, applicationId);
      await resource.reload();
    },
    repairJobProfile: async (mode: "reprocess_frozen" | "adopt_current" = "reprocess_frozen") => {
      await repairJobProfile(applicationId, mode);
      await scoringWorkflow.refresh();
      await resource.reload();
    },
    canApprove: resource.data?.availableActions.includes("approve_first_interview") ?? false,
    canReject: resource.data?.availableActions.includes("reject") ?? false
  };
}
