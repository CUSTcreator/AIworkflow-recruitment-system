import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";

import type { BusinessPermission } from "@/modules/auth/permissions";
import { hasBusinessPermission } from "@/modules/auth/permissions";
import { useAuth } from "@/modules/auth/AuthProvider";


export function PermissionGate({
  permission,
  children
}: {
  permission: BusinessPermission | BusinessPermission[];
  children: ReactNode;
}) {
  const { user } = useAuth();
  const permissions = Array.isArray(permission) ? permission : [permission];
  if (!permissions.some((code) => hasBusinessPermission(user, code))) {
    return <Navigate to="/" replace />;
  }
  return <>{children}</>;
}
