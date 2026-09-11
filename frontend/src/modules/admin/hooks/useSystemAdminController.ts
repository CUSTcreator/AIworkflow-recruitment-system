import { useCallback, useEffect, useState, type FormEvent } from "react";
import {
  createAdminRole,
  createAdminUser,
  deleteAdminRole,
  deleteAdminUser,
  getAdminAuditEvents,
  getAdminDepartments,
  getAdminJobs,
  getAdminRoles,
  getAdminUsers,
  getAdminWorkflows,
  getDeletedApplications,
  getObjectCleanupTasks,
  permanentlyDeleteApplication,
  retryObjectCleanup,
  resetAdminUserPassword,
  updateAdminRole,
  updateAdminUser,
  type AdminAuditEventQuery,
  type AdminDepartmentView,
  type AdminJobView,
  type AdminRoleView,
  type AdminRoleWrite,
  type AdminUserView,
  type AdminUserWrite,
  type AdminWorkflowView,
  type AuditEventView,
  type DeletedApplicationView,
  type ObjectCleanupTaskView,
  type WorkflowSummary
} from "@/modules/admin/api";
import { useAuth } from "@/modules/auth/AuthProvider";
import type { PermissionEffect, ResponsibilityBundleCode } from "@/modules/auth/permissions";

export type AdminSection = "users" | "organization" | "workflows" | "deleted" | "audit";

const emptyWorkflowSummary: WorkflowSummary = { pending: 0, running: 0, blocked: 0, completed: 0, failed: 0 };

function sameResponsibilitySettings(
  left: Partial<Record<ResponsibilityBundleCode, PermissionEffect>>,
  right: Partial<Record<ResponsibilityBundleCode, PermissionEffect>>
): boolean {
  const leftEntries = Object.entries(left).sort();
  const rightEntries = Object.entries(right).sort();
  return JSON.stringify(leftEntries) === JSON.stringify(rightEntries);
}

export function responsibilitySettingsForRole(
  role?: AdminRoleView
): Record<ResponsibilityBundleCode, PermissionEffect> {
  const allowed = new Set(role?.responsibilityBundles ?? []);
  const catalog = role?.responsibilityCatalog ?? [];
  return Object.fromEntries(
    catalog.map((item) => [item.code, allowed.has(item.code) ? "allow" : "deny"])
  ) as Record<ResponsibilityBundleCode, PermissionEffect>;
}

/** 账号表单只保存与角色默认职责不同的个人例外。 */
export function responsibilityDefaultEffect(
  role: AdminRoleView | undefined,
  code: ResponsibilityBundleCode
): PermissionEffect {
  return role?.responsibilityBundles.includes(code) ? "allow" : "deny";
}

export function useSystemAdminController() {
  const { token, user: currentUser } = useAuth();
  const [activeSection, setActiveSection] = useState<AdminSection>("users");
  const [users, setUsers] = useState<AdminUserView[]>([]);
  const [roles, setRoles] = useState<AdminRoleView[]>([]);
  const [departments, setDepartments] = useState<AdminDepartmentView[]>([]);
  const [jobs, setJobs] = useState<AdminJobView[]>([]);
  const [workflows, setWorkflows] = useState<AdminWorkflowView[]>([]);
  const [workflowSummary, setWorkflowSummary] = useState(emptyWorkflowSummary);
  const [auditEvents, setAuditEvents] = useState<AuditEventView[]>([]);
  const [workflowNextCursor, setWorkflowNextCursor] = useState<string>();
  const [workflowStatusFilter, setWorkflowStatusFilter] = useState("");
  const [auditNextCursor, setAuditNextCursor] = useState<string>();
  const [deletedNextCursor, setDeletedNextCursor] = useState<string>();
  const [auditQuery, setAuditQuery] = useState<AdminAuditEventQuery>({});
  const [deletedApplications, setDeletedApplications] = useState<DeletedApplicationView[]>([]);
  const [objectCleanupTasks, setObjectCleanupTasks] = useState<ObjectCleanupTaskView[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [userDialog, setUserDialog] = useState<{ userId?: string; form: AdminUserWrite }>();
  const [roleDialog, setRoleDialog] = useState<{
    roleId?: string;
    form: AdminRoleWrite;
    isSystem?: boolean;
    originalBusinessScope?: AdminRoleWrite["businessScope"];
    userCount?: number;
  }>();
  const [savingRole, setSavingRole] = useState(false);
  const [roleFormError, setRoleFormError] = useState("");
  const [passwordUser, setPasswordUser] = useState<AdminUserView>();
  const [confirmUserPassword, setConfirmUserPassword] = useState("");
  const [resetPassword, setResetPassword] = useState("");
  const [confirmResetPassword, setConfirmResetPassword] = useState("");
  const [userFormError, setUserFormError] = useState("");
  const [passwordFormError, setPasswordFormError] = useState("");
  const [savingUser, setSavingUser] = useState(false);
  const [resettingPassword, setResettingPassword] = useState(false);
  const [hardDeleteTarget, setHardDeleteTarget] = useState<DeletedApplicationView>();
  const [hardDeleteConfirm, setHardDeleteConfirm] = useState("");
  const [hardDeleting, setHardDeleting] = useState(false);

  const loadAll = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError("");
    const failures: string[] = [];
    const load = async (label: string, operation: () => Promise<void>) => {
      try { await operation(); }
      catch (reason) { failures.push(`${label}：${reason instanceof Error ? reason.message : "加载失败"}`); }
    };
    try {
      await Promise.all([
        load("账号", async () => setUsers(await getAdminUsers(token))),
        load("角色", async () => setRoles(await getAdminRoles(token))),
        load("部门", async () => setDepartments(await getAdminDepartments(token))),
        load("岗位", async () => setJobs(await getAdminJobs(token))),
        load("后台任务", async () => {
          const result = await getAdminWorkflows(token);
          setWorkflows(result.items); setWorkflowSummary(result.summary); setWorkflowNextCursor(result.nextCursor);
        }),
        load("操作日志", async () => {
          const page = await getAdminAuditEvents(token);
          setAuditEvents(page.items); setAuditNextCursor(page.nextCursor); setAuditQuery({});
        }),
        load("已删除申请", async () => {
          const page = await getDeletedApplications(token);
          setDeletedApplications(page.items); setDeletedNextCursor(page.nextCursor);
        }),
        load("文件清理任务", async () => setObjectCleanupTasks(await getObjectCleanupTasks(token)))
      ]);
      if (failures.length) setError(failures.join("；"));
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => { void loadAll(); }, [loadAll]);

  // 仅在管理员主动点击“加载更多”时请求下一页，避免管理页初次加载历史日志。
  /**
   * 管理员停留在后台任务或操作日志页时只刷新两类运行数据，不重复请求账号、岗位等
   * 静态管理数据，也不清空当前审计筛选条件。
   */
  const refreshOperations = useCallback(async () => {
    if (!token) return;
    try {
      const [workflowResult, auditPage] = await Promise.all([
        getAdminWorkflows(token, { status: workflowStatusFilter || undefined }),
        getAdminAuditEvents(token, auditQuery),
      ]);
      setWorkflows(workflowResult.items);
      setWorkflowSummary(workflowResult.summary);
      setWorkflowNextCursor(workflowResult.nextCursor);
      setAuditEvents(auditPage.items);
      setAuditNextCursor(auditPage.nextCursor);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "运行日志刷新失败");
    }
  }, [auditQuery, token, workflowStatusFilter]);

  const searchWorkflows = useCallback(async (status: string) => {
    if (!token) return;
    const result = await getAdminWorkflows(token, { status: status || undefined });
    setWorkflowStatusFilter(status);
    setWorkflows(result.items);
    setWorkflowSummary(result.summary);
    setWorkflowNextCursor(result.nextCursor);
  }, [token]);

  const loadMoreWorkflows = useCallback(async () => {
    if (!token || !workflowNextCursor) return;
    const page = await getAdminWorkflows(token, {
      status: workflowStatusFilter || undefined,
      cursor: workflowNextCursor
    });
    setWorkflows((current) => [...current, ...page.items]);
    setWorkflowNextCursor(page.nextCursor);
  }, [token, workflowNextCursor, workflowStatusFilter]);

  const retryCleanup = useCallback(async (cleanupTaskId: string) => {
    if (!token) return;
    setError("");
    try {
      await retryObjectCleanup(token, cleanupTaskId);
      setMessage("已重新执行文件清理。");
      setObjectCleanupTasks(await getObjectCleanupTasks(token));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "文件清理重试失败");
    }
  }, [token]);

  const loadMoreAuditEvents = useCallback(async () => {
    if (!token || !auditNextCursor) return;
    const page = await getAdminAuditEvents(token, { ...auditQuery, cursor: auditNextCursor });
    setAuditEvents((current) => [...current, ...page.items]);
    setAuditNextCursor(page.nextCursor);
  }, [auditNextCursor, auditQuery, token]);

  const loadMoreDeletedApplications = useCallback(async () => {
    if (!token || !deletedNextCursor) return;
    const page = await getDeletedApplications(token, deletedNextCursor);
    setDeletedApplications((current) => [...current, ...page.items]);
    setDeletedNextCursor(page.nextCursor);
  }, [deletedNextCursor, token]);

  const searchAuditEvents = useCallback(async (query: AdminAuditEventQuery) => {
    if (!token) return;
    const page = await getAdminAuditEvents(token, query);
    setAuditEvents(page.items);
    setAuditNextCursor(page.nextCursor);
    setAuditQuery(query);
  }, [token]);

  const saveUser = useCallback(async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!token || !userDialog) return;
    if (!userDialog.userId && userDialog.form.password !== confirmUserPassword) {
      setUserFormError("两次输入的密码不一致");
      return;
    }
    setError("");
    setUserFormError("");
    setSavingUser(true);
    try {
      const form = userDialog.form;
      const originalUser = userDialog.userId
        ? users.find((item) => item.userId === userDialog.userId)
        : undefined;
      const responsibilityOverridesChanged = !originalUser
        || form.roleId !== originalUser.roleId
        || !sameResponsibilitySettings(
          form.responsibilityOverrides ?? {},
          originalUser?.responsibilityOverrides ?? {}
        );
      const write = {
        username: form.username,
        displayName: form.displayName,
        roleId: form.roleId,
        departmentId: form.departmentId,
        businessScope: form.businessScope,
        isSystemAdmin: form.isSystemAdmin,
        isActive: form.isActive,
        ...(responsibilityOverridesChanged
          ? { responsibilityOverrides: form.responsibilityOverrides ?? {} }
          : {})
      };
      if (userDialog.userId) {
        await updateAdminUser(token, userDialog.userId, write);
        setMessage("账号信息已更新。");
      } else {
        await createAdminUser(token, { ...form, ...write, departmentId: form.departmentId });
        setMessage("账号已创建。");
      }
      setUserDialog(undefined);
      setConfirmUserPassword("");
      await loadAll();
    } catch (reason) {
      setUserFormError(reason instanceof Error ? reason.message : "账号保存失败");
    } finally {
      setSavingUser(false);
    }
  }, [confirmUserPassword, loadAll, token, userDialog, users]);

  const submitPasswordReset = useCallback(async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!token || !passwordUser) return;
    if (resetPassword !== confirmResetPassword) {
      setPasswordFormError("两次输入的密码不一致");
      return;
    }
    setError("");
    setPasswordFormError("");
    setResettingPassword(true);
    try {
      await resetAdminUserPassword(token, passwordUser.userId, resetPassword);
      setPasswordUser(undefined);
      setResetPassword("");
      setConfirmResetPassword("");
      setMessage("密码已重置。");
      await loadAll();
    } catch (reason) {
      setPasswordFormError(reason instanceof Error ? reason.message : "密码重置失败");
    } finally {
      setResettingPassword(false);
    }
  }, [confirmResetPassword, loadAll, passwordUser, resetPassword, token]);

  const editUser = useCallback((user: AdminUserView) => {
    setUserFormError("");
    setUserDialog({
      userId: user.userId,
      form: {
        username: user.username,
        displayName: user.displayName,
        roleId: user.roleId,
        departmentId: user.departmentId ?? "",
        businessScope: user.businessScope,
        // 角色默认职责已包含在 effectiveResponsibilityBundles 中，不能把它们
        // 当作个人覆盖提交，否则账号会被错误地保存成“全部项目均有例外”。
        responsibilityOverrides: { ...user.responsibilityOverrides },
        isSystemAdmin: user.isSystemAdmin,
        isActive: user.isActive
      }
    });
  }, []);

  const saveRole = useCallback(async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!token || !roleDialog) return;
    const scopeChanged = Boolean(
      roleDialog.roleId
      && roleDialog.originalBusinessScope
      && roleDialog.form.businessScope !== roleDialog.originalBusinessScope
    );
    if (scopeChanged && roleDialog.userCount && !window.confirm(
      `此操作会立即修改 ${roleDialog.userCount} 个账号的数据范围，确认继续吗？`
    )) {
      return;
    }
    setSavingRole(true);
    setRoleFormError("");
    try {
      if (roleDialog.roleId) {
        await updateAdminRole(token, roleDialog.roleId, roleDialog.form);
        setMessage("角色已更新。");
      } else {
        await createAdminRole(token, roleDialog.form);
        setMessage("角色已创建。");
      }
      setRoleDialog(undefined);
      await loadAll();
    } catch (reason) {
      setRoleFormError(reason instanceof Error ? reason.message : "角色保存失败");
    } finally {
      setSavingRole(false);
    }
  }, [loadAll, roleDialog, token]);

  const removeRole = useCallback(async (role: AdminRoleView) => {
    if (!token || role.isSystem || role.userCount > 0) return;
    if (!window.confirm(`确认删除角色“${role.name}”吗？`)) return;
    setError("");
    try {
      await deleteAdminRole(token, role.roleId);
      setMessage("角色已删除。");
      await loadAll();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "角色删除失败");
    }
  }, [loadAll, token]);

  const removeUser = useCallback(async (user: AdminUserView) => {
    if (!token || user.userId === currentUser?.userId || user.deletedAt) return;
    if (!window.confirm(`确认逻辑删除账号“${user.displayName}”吗？删除后不可登录、不可被岗位分配，但历史记录会保留。`)) return;
    setError("");
    try {
      await deleteAdminUser(token, user.userId);
      setMessage("账号已逻辑删除，历史记录仍可查询。 ");
      await loadAll();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "账号删除失败");
    }
  }, [currentUser?.userId, loadAll, token]);

  const confirmHardDelete = useCallback(async () => {
    if (!token || !hardDeleteTarget || hardDeleteConfirm.trim() !== hardDeleteTarget.candidateName || hardDeleting) return;
    setHardDeleting(true);
    setError("");
    try {
      const result = await permanentlyDeleteApplication(token, hardDeleteTarget.applicationId);
      setMessage(result.cleanupWarnings.length > 0
        ? "申请记录已彻底删除，部分文件可在本页重试清理。"
        : "申请及其关联数据已彻底删除。");
      setHardDeleteTarget(undefined);
      setHardDeleteConfirm("");
      await loadAll();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "彻底删除失败");
    } finally {
      setHardDeleting(false);
    }
  }, [hardDeleteConfirm, hardDeleteTarget, hardDeleting, loadAll, token]);

  return {
    token, currentUser, activeSection, setActiveSection, users, roles,
    departments, jobs, workflows, workflowSummary, workflowStatusFilter, auditEvents, workflowNextCursor, auditNextCursor, deletedNextCursor,
    deletedApplications, objectCleanupTasks, loading, error, setError, message, setMessage,
    userDialog, setUserDialog, roleDialog, setRoleDialog, savingRole,
    roleFormError, setRoleFormError, passwordUser, setPasswordUser,
    confirmUserPassword, setConfirmUserPassword, resetPassword,
    setResetPassword, confirmResetPassword, setConfirmResetPassword,
    userFormError, setUserFormError, passwordFormError, setPasswordFormError,
    savingUser, resettingPassword, hardDeleteTarget, setHardDeleteTarget,
    hardDeleteConfirm, setHardDeleteConfirm, hardDeleting, loadAll, refreshOperations, searchWorkflows, loadMoreWorkflows, loadMoreAuditEvents, loadMoreDeletedApplications, searchAuditEvents, retryCleanup, saveUser,
    submitPasswordReset, editUser, saveRole, removeRole, removeUser, confirmHardDelete
  };
}
