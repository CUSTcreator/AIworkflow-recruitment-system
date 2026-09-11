import type { ReactNode } from "react";
import { WorkflowActionsProvider } from "@/modules/recruitment_workflow/workflowActions";
import { AuthProvider } from "@/modules/auth/AuthProvider";
import { ToastProvider } from "@/shared/toast/ToastProvider";
import { ToastViewport } from "@/shared/ui/ToastViewport";

export function AppProviders({ children }: { children: ReactNode }) {
  return (
    <AuthProvider>
      <ToastProvider>
        <WorkflowActionsProvider>{children}</WorkflowActionsProvider>
        <ToastViewport />
      </ToastProvider>
    </AuthProvider>
  );
}
