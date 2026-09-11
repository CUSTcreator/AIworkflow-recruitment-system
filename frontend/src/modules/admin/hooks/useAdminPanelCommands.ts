import { useCallback } from "react";
import {
  createAdminDepartment,
  deleteAdminDepartment,
  deleteAdminJob,
  retryAdminWorkflow,
  updateAdminDepartment,
  updateAdminJob
} from "@/modules/admin/api";
import { useAuth } from "@/modules/auth/AuthProvider";

interface PanelCommandCallbacks {
  onChanged: () => Promise<void>;
  onError: (value: string) => void;
  onMessage: (value: string) => void;
}

export function useAdminPanelCommands({ onChanged, onError, onMessage }: PanelCommandCallbacks) {
  const { token } = useAuth();

  const execute = useCallback(async (action: (token: string) => Promise<unknown>, success: string) => {
    if (!token) return;
    onError("");
    try {
      await action(token);
      onMessage(success);
      await onChanged();
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "操作失败");
    }
  }, [onChanged, onError, onMessage, token]);

  return {
    createDepartment: (name: string) => execute(
      (activeToken) => createAdminDepartment(activeToken, name),
      "部门已创建。"
    ),
    updateDepartment: (
      departmentId: string,
      input: { name?: string },
      success: string
    ) => execute(
      (activeToken) => updateAdminDepartment(activeToken, departmentId, input),
      success
    ),
    deleteDepartment: (departmentId: string) => execute(
      (activeToken) => deleteAdminDepartment(activeToken, departmentId),
      "部门及其岗位已删除，历史记录仍可查询。"
    ),
    deleteJob: (jobId: string) => execute(
      (activeToken) => deleteAdminJob(activeToken, jobId),
      "岗位已删除，历史候选人记录仍可查询。"
    ),
    deleteJobs: async (jobIds: string[]) => {
      if (!token || !jobIds.length) return { succeeded: [], failed: [] };
      onError("");
      const results = await Promise.allSettled(jobIds.map((jobId) => deleteAdminJob(token, jobId)));
      const succeeded = jobIds.filter((_jobId, index) => results[index].status === "fulfilled");
      const failed = jobIds.filter((_jobId, index) => results[index].status === "rejected");
      if (failed.length) onError(`${failed.length} 个岗位删除失败。`);
      else onMessage(`已删除 ${succeeded.length} 个岗位。`);
      await onChanged();
      return { succeeded, failed };
    },

    updateJob: (
      jobId: string,
      input: { status?: "setup_pending" | "open" | "closed"; headcount?: number; hiringManagerId?: string; departmentRecruiterId?: string }
    ) => execute(
      (activeToken) => updateAdminJob(activeToken, jobId, input),
      "岗位配置已更新。"
    ),
    retryWorkflow: (workflowRunId: string, action: "retry" | "resume") => execute(
      (activeToken) => retryAdminWorkflow(
        activeToken,
        workflowRunId,
        action === "resume" ? "管理员确认后继续执行" : "管理员确认后人工重试"
      ),
      action === "resume"
        ? "任务已从待确认步骤继续执行。"
        : "已创建新的重试任务，原失败记录保持不变。"
    )
  };
}
