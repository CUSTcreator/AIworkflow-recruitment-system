import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";

import { useAuth } from "@/modules/auth/AuthProvider";


export function AdminGate({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  if (!user?.isSystemAdmin || user.businessScope !== "organization") {
    return <Navigate to="/" replace />;
  }
  return <>{children}</>;
}
