import { useCallback } from "react";
import { getFirstInterviewPlanView, type FirstInterviewPlanReadModel } from "../firstInterviewApi";
import {
  useWorkflowPolling,
  type WorkflowRunStatus
} from "@/shared/hooks/useWorkflowPolling";

type PollingWorkspace = FirstInterviewPlanReadModel & {
  status: WorkflowRunStatus;
};

/**
 * 题单规划轮询：Workflow 尚未发布 PlanVersion 时持续刷新；一旦返回 plan 即停止轮询并交给编辑页。
 * 轮询只读取后端聚合 DTO，不在浏览器内补题或推导题目关联字段。
 */
export function useFirstInterviewPlanWorkspace(token: string | null, applicationId: string) {
  const load = useCallback(async (): Promise<PollingWorkspace> => {
    if (!token) throw new Error("登录状态已失效");
    const workspace = await getFirstInterviewPlanView(token, applicationId);
    return {
      ...workspace,
      status: workspace.plan ? "ready" : workspace.planningState?.status ?? "not_started"
    };
  }, [applicationId, token]);

  const state = useWorkflowPolling(load, {
    enabled: Boolean(token),
    intervalMs: 2_000
  });

  return {
    workspace: state.data,
    loading: !state.data && !state.error,
    loadError: state.error,
    polling: state.polling,
    reload: state.refresh
  };
}
