import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "@/modules/auth/AuthProvider";
import { App } from "./App";

export function ProtectedApp() {
  const { user, checking } = useAuth();
  const location = useLocation();
  if (checking) return <div className="flex min-h-screen items-center justify-center text-sm text-muted">正在校验登录状态</div>;
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  return <App />;
}
