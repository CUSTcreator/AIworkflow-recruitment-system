/**
 * 招聘流程前端命令门面。
 *
 * 统一封装 Application、Assessment 与 Interviews 三个模块的跨阶段操作；
 * 不归属于单一业务实体，页面应通过本文件调用而非直接拼装流程命令。
 */
import { createContext, createElement, useCallback, useContext, useMemo, type ReactNode } from "react";
import type { FinalDecision } from "@/modules/applications/contracts";
import type {
  FirstInterviewProgressDraft,
  InterviewPlan,
  SecondInterviewProgressDraft
} from "@/modules/interviews/contracts";
import type { CompleteFirstInterviewRequest, CompleteSecondInterviewRequest } from "@/modules/interviews/commands";
import {
  applicationCommands,
  type ApplicationCommandResponse,
  type JobProfileRecoveryMode,
} from "@/modules/applications/commands";
import { useAuth } from "@/modules/auth/AuthProvider";
import { useToast } from "@/shared/toast/ToastProvider";

interface WorkflowActionsValue {
  runScoring: (applicationId: string) => Promise<void>;
  repairJobProfile: (applicationId: string, mode?: JobProfileRecoveryMode) => Promise<string | undefined>;
  runFirstInterviewPlanning: (applicationId: string) => Promise<string | undefined>;
  continueFirstInterviewManually: (applicationId: string) => Promise<string | undefined>;
  approveFirstInterview: (applicationId: string) => Promise<string | undefined>;
  makeDepartmentDecision: (applicationId: string, decision: "暂缓" | "不推进" | "人工复核") => Promise<string | undefined>;
  saveFirstInterviewPlanDraft: (applicationId: string, plan: InterviewPlan) => Promise<boolean>;
  confirmFirstInterviewPlan: (applicationId: string, plan: InterviewPlan) => Promise<string | undefined>;
  saveFirstInterviewProgress: (applicationId: string, draft: FirstInterviewProgressDraft, options?: { silent?: boolean }) => Promise<boolean>;
  completeFirstInterview: (applicationId: string, request: CompleteFirstInterviewRequest) => Promise<string | undefined>;
  retryPostFirstScoring: (applicationId: string) => Promise<string | undefined>;
  rebuildScreeningAssessment: (applicationId: string) => Promise<string | undefined>;
  repairPostFirstScoring: (applicationId: string, rawNotes: string) => Promise<string | undefined>;
  approveSecondInterview: (applicationId: string) => Promise<string | undefined>;
  makeHrDecision: (applicationId: string, decision: "暂缓" | "不推进" | "补充验证") => Promise<string | undefined>;
  saveSecondInterviewProgress: (applicationId: string, draft: SecondInterviewProgressDraft, options?: { silent?: boolean }) => Promise<boolean>;
  completeSecondInterview: (applicationId: string, request: CompleteSecondInterviewRequest) => Promise<string | undefined>;
  retryPostSecondScoring: (applicationId: string) => Promise<string | undefined>;
  repairPostSecondScoring: (applicationId: string, rawNotes: string) => Promise<string | undefined>;
  makeFinalDecision: (applicationId: string, decision: FinalDecision) => Promise<string | undefined>;
}

const WorkflowActionsContext = createContext<WorkflowActionsValue | undefined>(undefined);

export function WorkflowActionsProvider({ children }: { children: ReactNode }) {
  const { token } = useAuth();
  const toast = useToast();

  const run = useCallback(async (
    command: () => Promise<ApplicationCommandResponse>
  ): Promise<string | undefined> => {
    if (!token) return undefined;
    try {
      const result = await command();
      toast.success(result.message ?? "流程操作已完成");
      // 空字符串表示命令已成功但无需跳转；undefined 仅表示未执行成功。
      return result.next_route ?? "";
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "流程操作失败");
      return undefined;
    }
  }, [token, toast]);

  const save = useCallback(async (command: () => Promise<unknown>, options?: { silent?: boolean }): Promise<boolean> => {
    if (!token) return false;
    try {
      await command();
      if (!options?.silent) toast.success("记录已保存");
      return true;
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "保存失败");
      return false;
    }
  }, [token, toast]);

  const value = useMemo<WorkflowActionsValue>(() => ({
    runScoring: async (applicationId) => { await run(() => applicationCommands.runScoring(token!, applicationId)); },
    repairJobProfile: (applicationId, mode) => run(() => applicationCommands.repairJobProfile(token!, applicationId, mode)),
    runFirstInterviewPlanning: (applicationId) => run(() => applicationCommands.runFirstInterviewPlanning(token!, applicationId)),
    continueFirstInterviewManually: (applicationId) => run(() => applicationCommands.continueFirstInterviewManually(token!, applicationId)),
    approveFirstInterview: (applicationId) => run(() => applicationCommands.approveFirstInterview(token!, applicationId)),
    makeDepartmentDecision: (applicationId, decision) => run(() => applicationCommands.departmentDecision(token!, applicationId, decision)),
    saveFirstInterviewPlanDraft: (applicationId, plan) => save(() => applicationCommands.saveFirstInterviewPlanDraft(token!, applicationId, plan)),
    confirmFirstInterviewPlan: (applicationId, plan) => run(() => applicationCommands.confirmFirstInterviewPlan(token!, applicationId, plan)),
    saveFirstInterviewProgress: (applicationId, draft, options) => save(() => applicationCommands.saveFirstInterviewProgress(token!, applicationId, draft), options),
    completeFirstInterview: async (applicationId, request) => {
      return run(() => applicationCommands.completeFirstInterview(token!, applicationId, request));
    },
    retryPostFirstScoring: (applicationId) => run(() => applicationCommands.retryPostFirstScoring(token!, applicationId)),
    rebuildScreeningAssessment: (applicationId) => run(() => applicationCommands.rebuildScreeningAssessment(token!, applicationId)),
    repairPostFirstScoring: (applicationId, rawNotes) => run(() => applicationCommands.repairPostFirstScoring(token!, applicationId, rawNotes)),
    approveSecondInterview: (applicationId) => run(() => applicationCommands.approveSecondInterview(token!, applicationId)),
    makeHrDecision: (applicationId, decision) => run(() => applicationCommands.hrDecision(token!, applicationId, decision)),
    saveSecondInterviewProgress: (applicationId, draft, options) => save(() => applicationCommands.saveSecondInterviewProgress(token!, applicationId, draft), options),
    completeSecondInterview: async (applicationId, request) => {
      return run(() => applicationCommands.completeSecondInterview(token!, applicationId, request));
    },
    retryPostSecondScoring: (applicationId) => run(() => applicationCommands.retryPostSecondScoring(token!, applicationId)),
    repairPostSecondScoring: (applicationId, rawNotes) => run(() => applicationCommands.repairPostSecondScoring(token!, applicationId, rawNotes)),
    makeFinalDecision: (applicationId, decision) => run(() => applicationCommands.finalDecision(token!, applicationId, decision))
  }), [run, save, token]);

  return createElement(WorkflowActionsContext.Provider, { value }, children);
}

export function useWorkflowActions(): WorkflowActionsValue {
  const context = useContext(WorkflowActionsContext);
  if (!context) throw new Error("useWorkflowActions must be used within WorkflowActionsProvider");
  return context;
}
