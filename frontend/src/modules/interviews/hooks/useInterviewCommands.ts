import { useCallback } from "react";
import { useWorkflowActions } from "@/modules/recruitment_workflow/workflowActions";
import { getEvidenceDetail } from "@/modules/assessment/api";
import { useAuth } from "@/modules/auth/AuthProvider";
import {
  downloadFirstInterviewGuide,
  previewFirstInterviewGuide
} from "@/modules/interviews/interviewGuideApi";

export function useInterviewCommands() {
  const actions = useWorkflowActions();
  const { token } = useAuth();

  const loadEvidence = useCallback((applicationId: string, evidenceId: string) => {
    if (!token) return Promise.reject(new Error("登录状态已失效"));
    return getEvidenceDetail(token, applicationId, evidenceId);
  }, [token]);

  const previewGuide = useCallback((applicationId: string, draft = false) => {
    if (!token) return Promise.reject(new Error("登录状态已失效"));
    return previewFirstInterviewGuide(token, applicationId, draft);
  }, [token]);

  const downloadGuide = useCallback((applicationId: string, draft = false) => {
    if (!token) return Promise.reject(new Error("登录状态已失效"));
    return downloadFirstInterviewGuide(token, applicationId, draft);
  }, [token]);

  return { ...actions, token, loadEvidence, previewGuide, downloadGuide };
}