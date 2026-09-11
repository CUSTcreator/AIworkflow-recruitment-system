import type { BusinessPermission } from "./permissions";

export type Role = string;

export interface AuthUser {
  userId: string;
  username: string;
  displayName: string;
  role: Role;
  roleId: string;
  roleName: string;
  departmentId?: string;
  businessScope: "department" | "organization";
  permissions: BusinessPermission[];
  isSystemAdmin: boolean;
  mustChangePassword: boolean;
}

export interface LoginCommand {
  username: string;
  password: string;
}

export interface LoginResponse {
  accessToken: string;
  user: AuthUser;
}
