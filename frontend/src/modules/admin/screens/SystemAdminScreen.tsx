import {
  Activity,
  Building2,
  Check,
  Eye,
  EyeOff,
  Info,
  KeyRound,
  LockKeyhole,
  Plus,
  RefreshCw,
  Save,
  ScrollText,
  ShieldCheck,
  Trash2,
  Users
} from "lucide-react";
import { Fragment, useCallback, useEffect, useMemo, useState, type FormEvent, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import type {
  AdminDepartmentView,
  AdminJobView,
  AdminRoleView,
  AdminRoleWrite,
  AdminUserView,
  AdminUserWrite,
  AdminAuditEventQuery,
  AdminWorkflowView,
  AuditEventView,
  DeletedApplicationView,
  ObjectCleanupTaskView,
  WorkflowSummary,
  WorkflowExecutionEventView
} from "@/modules/admin/api";
import { Badge } from "@/shared/ui/Badge";
import { Button } from "@/shared/ui/Button";
import { PageHeader } from "@/shared/ui/PageHeader";
import { Section } from "@/shared/ui/Section";
import { StatCard } from "@/shared/ui/StatCard";
import { RowActionMenu } from "@/shared/ui/RowActionMenu";
import { createAdminHardScreeningCriterion, deleteAdminHardScreeningCriterion, getAdminHardScreeningCriteria, getAdminWorkflowExecutionEvents, resetAdminHardScreeningCriteria, updateAdminHardScreeningCriterion, type AdminHardScreeningCatalogCriterion } from "@/modules/admin/api";
import { useAuth } from "@/modules/auth/AuthProvider";
import { displayRoleName } from "@/modules/auth/accessPolicy";
import type { PermissionEffect, ResponsibilityBundleCode } from "@/modules/auth/permissions";
import {
  responsibilityDefaultEffect,
  useSystemAdminController,
  type AdminSection
} from "@/modules/admin/hooks/useSystemAdminController";
import { useAdminPanelCommands } from "@/modules/admin/hooks/useAdminPanelCommands";



const adminSections: Array<{
  value: AdminSection;
  label: string;
  icon: typeof Users;
}> = [
  { value: "users", label: "账号与权限", icon: Users },
  { value: "organization", label: "部门与岗位", icon: Building2 },
  { value: "workflows", label: "后台任务", icon: Activity },
  { value: "deleted", label: "已删除申请", icon: Trash2 },
  { value: "audit", label: "操作日志", icon: ScrollText }
];


type AuditCategory = "all" | "account" | "organization" | "recruitment" | "task";

const auditActionLabels: Record<string, string> = {
  "admin.user.create": "创建账号",
  "admin.user.update": "修改账号",
  "admin.user.reset_password": "重置密码",
  "admin.user.delete": "删除账号",
  "admin.role.create": "创建角色",
  "admin.role.update": "修改角色",
  "admin.role.delete": "删除角色",
  "admin.department.create": "创建部门",
  "admin.department.update": "修改部门",
  "admin.department.delete": "删除部门",
  "admin.job.update": "修改岗位",
  "job.delete": "删除岗位",
  "admin.workflow.retry": "人工重试任务",
  "admin.workflow.resume_blocked": "继续待确认任务",
  "admin.object_cleanup.retry": "重试文件清理",
  "hard_screening.catalog.create": "新增硬筛条件",
  "hard_screening.catalog.update": "修改硬筛条件",
  "hard_screening.catalog.delete": "删除硬筛条件",
  "hard_screening.catalog.reset": "重置硬筛条件",
  "hard_screening.policy.update": "修改硬筛策略",
  "hard_screening.review": "人工复核硬筛",
  "interview.first.submit": "提交一面面评",
  "interview.second.submit": "提交二面面评",
  "application.decision": "保存招聘决策",
  "application.delete": "删除岗位申请",
  "application.hard_delete": "彻底删除申请",
  "candidate_document.upload": "上传候选人文件",
  "candidate_document.rename": "重命名候选人文件",
  "candidate_document.delete": "删除候选人文件"
};

const auditTargetLabels: Record<string, string> = {
  user: "账号",
  role: "角色",
  department: "部门",
  job: "岗位",
  workflow_run: "后台任务",
  application: "岗位申请",
  application_document: "候选人文件",
  object_cleanup_task: "文件清理任务"
};

const taskTypeLabels: Record<string, string> = {
  hard_screening_workflow: "硬性条件检查",
  scoring_workflow: "初步筛选评估",
  first_interview_planning_workflow: "生成一面题单",
  post_first_scoring_workflow: "更新一面后评估",
  post_second_scoring_workflow: "更新二面后评估",
  job_document_import_workflow: "导入岗位文件",
  resume_document_import_workflow: "导入简历文件",
  candidate_routing_workflow: "匹配候选人岗位",
  job_profile_compilation_workflow: "生成岗位能力画像"
};

const workflowSubjectLabels: Record<string, string> = {
  application: "岗位申请",
  resume_submission: "候选人简历",
  job_version: "招聘岗位",
  job_document_import: "岗位文件"
};

const emptyUserForm: AdminUserWrite = {
  username: "",
  displayName: "",
  roleId: "department_manager",
  departmentId: "",
  businessScope: "department",
  responsibilityOverrides: {},
  isSystemAdmin: false,
  password: ""
};

export function SystemAdminScreen() {
  const [searchParams] = useSearchParams();
  const {
    token, currentUser, activeSection, setActiveSection, users, roles,
    departments, jobs, workflows, workflowSummary, workflowStatusFilter, auditEvents, workflowNextCursor, auditNextCursor, deletedNextCursor,
    deletedApplications, objectCleanupTasks, loading, error, setError, message, setMessage,
    userDialog, setUserDialog, roleDialog, setRoleDialog, savingRole,
    roleFormError, setRoleFormError, passwordUser, setPasswordUser,
    confirmUserPassword, setConfirmUserPassword, resetPassword,
    setResetPassword, confirmResetPassword, setConfirmResetPassword,
    userFormError, setUserFormError, passwordFormError, setPasswordFormError, savingUser,
    resettingPassword, hardDeleteTarget, setHardDeleteTarget,
    hardDeleteConfirm, setHardDeleteConfirm, hardDeleting, loadAll, refreshOperations, searchWorkflows, loadMoreWorkflows, loadMoreAuditEvents, loadMoreDeletedApplications, searchAuditEvents, retryCleanup,
    saveUser, submitPasswordReset, editUser, saveRole, removeRole, removeUser,
    confirmHardDelete
  } = useSystemAdminController();
  const [showPersonalOverrides, setShowPersonalOverrides] = useState(false);
  // 职责包定义来自后端权限目录，避免浏览器再维护一份会漂移的权限组合。
  const responsibilityCatalog = roles[0]?.responsibilityCatalog
    ?? users[0]?.responsibilityCatalog
    ?? [];
  const selectedUserRole = userDialog
    ? roles.find((role) => role.roleId === userDialog.form.roleId)
    : undefined;

  useEffect(() => {
    if (searchParams.get("section") === "organization") {
      setActiveSection("organization");
    }
  }, [searchParams, setActiveSection]);
  const roleRequiresOrganizationScope = Boolean(
    roleDialog?.form.responsibilityBundles.some((code) => (
      responsibilityCatalog.find((item) => item.code === code)?.requiredBusinessScope
        === "organization"
    ))
  );

  useEffect(() => {
    if (!token || !["workflows", "audit"].includes(activeSection)) return;
    // 管理员正在查看运行信息时，定时拉取最新一页；任务明细仍只在打开弹窗后轮询。
    void refreshOperations();
    const timer = window.setInterval(() => void refreshOperations(), 10_000);
    return () => window.clearInterval(timer);
  }, [activeSection, refreshOperations, token]);

  return (
    <>
      <PageHeader title="系统管理" />
      <div className="mx-auto grid max-w-6xl gap-4 lg:grid-cols-[190px_minmax(0,1fr)]">
        <aside className="h-fit rounded-md border border-line bg-white p-2 shadow-sm">
          <div className="mb-2 flex items-center gap-2 px-2 py-2 text-xs font-semibold uppercase tracking-wide text-muted">
            <ShieldCheck size={15} />管理员功能
          </div>
          <nav className="grid gap-1 sm:grid-cols-5 lg:grid-cols-1">
            {adminSections.map((item) => {
              const Icon = item.icon;
              return (
                <button
                  key={item.value}
                  type="button"
                  className={`flex items-center gap-2 rounded-md px-3 py-2.5 text-left text-sm font-medium transition ${
                    activeSection === item.value
                      ? "bg-blue-50 text-blue-700"
                      : "text-slate-600 hover:bg-slate-50 hover:text-slate-900"
                  }`}
                  onClick={() => setActiveSection(item.value)}
                >
                  <Icon size={17} />
                  {item.label}
                </button>
              );
            })}
          </nav>
        </aside>

        <main className="min-w-0">
          {error ? (
            <div className="mb-3 rounded-md border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
              {error}
            </div>
          ) : null}
          {message ? (
            <div className="mb-3 rounded-md border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
              {message}
            </div>
          ) : null}
          {loading ? (
            <div className="rounded-md border border-dashed border-line bg-white p-10 text-center text-sm text-muted">
              正在加载系统管理数据…
            </div>
          ) : activeSection === "users" ? (
            <div className="space-y-4">
              <RoleManagementPanel
                roles={roles}
                onCreate={() => {
                  setRoleFormError("");
                  setRoleDialog({
                    form: {
                      name: "",
                      businessScope: "department",
                      responsibilityBundles: []
                    }
                  });
                }}
                onEdit={(role) => {
                  setRoleFormError("");
                  setRoleDialog({
                    roleId: role.roleId,
                    isSystem: role.isSystem,
                    originalBusinessScope: role.businessScope,
                    userCount: role.userCount,
                    form: {
                      name: role.name,
                      businessScope: role.businessScope,
                      responsibilityBundles: [...role.responsibilityBundles],
                      isActive: role.isActive
                    }
                  });
                }}
                onDelete={(role) => void removeRole(role)}
              />
              <UserManagementPanel
                users={users}
                currentUserId={currentUser?.userId}
                onCreate={() => {
                  const role = roles.find((item) => item.roleId === "department_manager" && item.isActive)
                    ?? roles.find((item) => item.isActive);
                  if (!role) return;
                  setConfirmUserPassword("");
                  setUserFormError("");
                  setShowPersonalOverrides(false);
                  setUserDialog({
                    form: {
                      ...emptyUserForm,
                      roleId: role.roleId,
                      businessScope: role.businessScope,
                      responsibilityOverrides: {}
                    }
                  });
                }}
                onEdit={(user) => {
                  setShowPersonalOverrides(false);
                  editUser(user);
                }}
                onDelete={(user) => void removeUser(user)}
                onResetPassword={(user) => {
                  setPasswordUser(user);
                  setResetPassword("");
                  setConfirmResetPassword("");
                  setPasswordFormError("");
                }}
              />
            </div>
          ) : activeSection === "organization" ? (
            <OrganizationManagementPanel
              users={users}
              departments={departments}
              jobs={jobs}
              onChanged={loadAll}
              onError={setError}
              onMessage={setMessage}
            />
          ) : activeSection === "workflows" ? (
            <WorkflowManagementPanel
              token={token}
              summary={workflowSummary}
              workflows={workflows}
              statusFilter={workflowStatusFilter}
              hasMore={Boolean(workflowNextCursor)}
              onLoadMore={loadMoreWorkflows}
              onRefresh={refreshOperations}
              onStatusFilterChange={searchWorkflows}
              onChanged={refreshOperations}
              onError={setError}
              onMessage={setMessage}
            />
          ) : activeSection === "deleted" ? (
            <DeletedApplicationsPanel
              items={deletedApplications}
              cleanupTasks={objectCleanupTasks}
              hasMore={Boolean(deletedNextCursor)}
              onLoadMore={loadMoreDeletedApplications}
              onRetryCleanup={retryCleanup}
              onDelete={(item) => {
                setHardDeleteTarget(item);
                setHardDeleteConfirm("");
              }}
            />
          ) : (
            <AuditManagementPanel users={users} events={auditEvents} hasMore={Boolean(auditNextCursor)} onLoadMore={loadMoreAuditEvents} onRefresh={refreshOperations} onSearch={searchAuditEvents} />
          )}
        </main>
      </div>

      {roleDialog ? (
        <Dialog
          title={roleDialog.roleId ? "编辑角色" : "新增角色"}
          onClose={() => setRoleDialog(undefined)}
          wide
        >
          <form className="space-y-4" onSubmit={saveRole}>
            <div className="grid gap-3 sm:grid-cols-2">
              <AdminField label="角色名称">
                <input
                  required
                  className="admin-input"
                  value={roleDialog.form.name}
                  onChange={(event) => setRoleDialog({
                    ...roleDialog,
                    form: { ...roleDialog.form, name: event.target.value }
                  })}
                />
              </AdminField>
              <AdminField label="数据范围">
                <select
                  className="admin-input"
                  disabled={roleRequiresOrganizationScope}
                  title={roleRequiresOrganizationScope
                    ? "已选择仅全公司范围可用的职责"
                    : undefined}
                  value={roleDialog.form.businessScope}
                  onChange={(event) => setRoleDialog({
                    ...roleDialog,
                    form: {
                      ...roleDialog.form,
                      businessScope: event.target.value as AdminRoleWrite["businessScope"]
                    }
                  })}
                >
                  <option value="department">仅本部门</option>
                  <option value="organization">全公司</option>
                </select>
              </AdminField>
              {roleDialog.roleId && !roleDialog.isSystem ? (
                <AdminField label="角色状态">
                  <select
                    className="admin-input"
                    value={roleDialog.form.isActive === false ? "inactive" : "active"}
                    onChange={(event) => setRoleDialog({
                      ...roleDialog,
                      form: { ...roleDialog.form, isActive: event.target.value === "active" }
                    })}
                  >
                    <option value="active">启用</option>
                    <option value="inactive">停用</option>
                  </select>
                </AdminField>
              ) : null}
            </div>
            {roleDialog.roleId
              && roleDialog.originalBusinessScope
              && roleDialog.originalBusinessScope !== roleDialog.form.businessScope
              && roleDialog.userCount ? (
                <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
                  保存后会立即同步修改 {roleDialog.userCount} 个绑定账号的数据范围。
                </div>
              ) : null}
            <div className="border-t border-line pt-5">
              <div className="mb-4">
                <div className="text-sm font-semibold text-ink">角色默认职责</div>
                <p className="mt-1 text-xs leading-5 text-muted">勾选该角色默认负责的业务；账号可在此基础上设置个人例外。</p>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                {responsibilityCatalog.map((responsibility) => {
                  const checked = roleDialog.form.responsibilityBundles.includes(responsibility.code);
                  const requiresOrganizationScope = responsibility.requiredBusinessScope === "organization";
                  const unavailable = requiresOrganizationScope
                    && roleDialog.form.businessScope !== "organization";
                  return (
                    <label
                      key={responsibility.code}
                      className={`flex min-h-[78px] items-start gap-3 rounded-md border px-4 py-3.5 text-sm transition ${
                        unavailable
                          ? "cursor-not-allowed border-slate-200 bg-slate-50 text-slate-400"
                          : checked
                            ? "border-blue-300 bg-blue-50/70 shadow-[inset_3px_0_0_#2563eb]"
                            : "cursor-pointer border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50/60"
                      }`}
                      title={unavailable ? "该职责仅支持全公司数据范围角色" : undefined}
                    >
                      <input
                        type="checkbox"
                        className="mt-0.5 h-4 w-4 shrink-0 accent-blue-600"
                        checked={checked}
                        disabled={unavailable}
                        onChange={(event) => setRoleDialog({
                          ...roleDialog,
                          form: {
                            ...roleDialog.form,
                            responsibilityBundles: changeRoleResponsibility(
                              roleDialog.form.responsibilityBundles,
                              responsibility.code,
                              event.target.checked
                            )
                          }
                        })}
                      />
                      <span className="min-w-0">
                        <span className={`block font-semibold ${unavailable ? "text-slate-400" : "text-slate-800"}`}>{responsibility.label}</span>
                        <span className="mt-1 block text-xs leading-5 text-muted">{responsibility.description}</span>
                        {requiresOrganizationScope ? (
                          <span className="mt-1 block text-xs text-amber-700">仅全公司范围</span>
                        ) : null}
                      </span>
                    </label>
                  );
                })}
              </div>
              <CandidateDeletionPermissionNote />
            </div>
            {roleFormError ? <p className="text-sm text-rose-700" role="alert">{roleFormError}</p> : null}
            <div className="flex justify-end gap-2">
              <Button type="button" onClick={() => setRoleDialog(undefined)}>取消</Button>
              <Button type="submit" variant="primary" disabled={savingRole}>
                <Save size={16} />{savingRole ? "保存中…" : "保存"}
              </Button>
            </div>
          </form>
        </Dialog>
      ) : null}

      {userDialog ? (
        <Dialog
          title={userDialog.userId ? "编辑账号" : "新增账号"}
          onClose={() => setUserDialog(undefined)}
          wide
        >
          <form className="space-y-4" onSubmit={saveUser}>
            <div className="grid gap-3 sm:grid-cols-2">
              <AdminField label="登录账号">
                <input
                  required
                  name="username"
                  autoComplete="username"
                  autoCapitalize="none"
                  spellCheck={false}
                  className="admin-input bg-blue-50"
                  value={userDialog.form.username}
                  onChange={(event) => setUserDialog({
                    ...userDialog,
                    form: { ...userDialog.form, username: event.target.value }
                  })}
                />
              </AdminField>
              <AdminField label="显示姓名">
                <input
                  required
                  name="displayName"
                  autoComplete="off"
                  className="admin-input"
                  value={userDialog.form.displayName}
                  onChange={(event) => setUserDialog({
                    ...userDialog,
                    form: { ...userDialog.form, displayName: event.target.value }
                  })}
                />
              </AdminField>
              <AdminField label="业务角色">
                <select
                  className="admin-input"
                  value={userDialog.form.roleId}
                  onChange={(event) => {
                    const role = roles.find((item) => item.roleId === event.target.value);
                    if (!role) return;
                    setUserDialog({
                      ...userDialog,
                      form: {
                        ...userDialog.form,
                        roleId: role.roleId,
                        businessScope: role.businessScope,
                        responsibilityOverrides: {},
                        isSystemAdmin: role.businessScope === "organization"
                          ? userDialog.form.isSystemAdmin
                          : false
                      }
                    });
                  }}
                >
                  {roles.filter((item) => item.isActive || item.roleId === userDialog.form.roleId).map((item) => (
                    <option key={item.roleId} value={item.roleId}>{item.name}</option>
                  ))}
                </select>
              </AdminField>
              <AdminField label="所属部门">
                <select
                  className="admin-input"
                  required
                  value={userDialog.form.departmentId ?? ""}
                  onChange={(event) => setUserDialog({
                    ...userDialog,
                    form: { ...userDialog.form, departmentId: event.target.value }
                  })}
                >
                  <option value="">请选择部门</option>
                  {departments.filter((item) => item.lifecycleStatus === "normal").map((item) => (
                    <option key={item.departmentId} value={item.departmentId}>{item.name}</option>
                  ))}
                </select>
              </AdminField>
              <AdminField label="查看候选人范围">
                <select
                  className="admin-input"
                  disabled
                  value={userDialog.form.businessScope}
                >
                  <option value="department">仅本部门</option>
                  <option value="organization">全公司</option>
                </select>
              </AdminField>
              {!userDialog.userId ? (
                <AdminField label="密码">
                  <PasswordInput
                    name="password"
                    value={userDialog.form.password ?? ""}
                    onChange={(value) => setUserDialog({
                      ...userDialog,
                      form: { ...userDialog.form, password: value }
                    })}
                  />
                </AdminField>
              ) : null}
              {!userDialog.userId ? (
                <AdminField label="确认密码">
                  <PasswordInput
                    name="confirmPassword"
                    value={confirmUserPassword}
                    onChange={setConfirmUserPassword}
                  />
                </AdminField>
              ) : null}
            </div>
            <div className="border-t border-line pt-5">
              <div className="mb-4 flex items-start justify-between gap-3">
                <div>
                  <div className="text-sm font-semibold text-ink">角色默认职责</div>
                  <p className="mt-1 text-xs leading-5 text-muted">以下职责随业务角色自动变化；需要特殊调整时再设置个人例外。</p>
                </div>
                <Badge className="border-blue-200 bg-blue-50 text-blue-700">{selectedUserRole?.responsibilityBundles.length ?? 0} 项允许</Badge>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                {(selectedUserRole?.responsibilityBundles ?? []).map((code) => {
                  const responsibility = responsibilityCatalog.find((item) => item.code === code);
                  return responsibility ? (
                    <div key={code} className="flex min-h-[78px] items-start gap-3 rounded-md border border-blue-200 bg-blue-50/70 px-4 py-3.5 shadow-[inset_3px_0_0_#2563eb]">
                      <span className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded bg-blue-600 text-white">
                        <Check size={12} strokeWidth={3} />
                      </span>
                      <span className="min-w-0">
                        <span className="block text-sm font-semibold text-slate-800">{responsibility.label}</span>
                        <span className="mt-1 block text-xs leading-5 text-muted">{responsibility.description}</span>
                      </span>
                    </div>
                  ) : null;
                })}
              </div>
              {(selectedUserRole?.responsibilityBundles.length ?? 0) === 0 ? (
                <div className="rounded-md border border-dashed border-slate-300 px-4 py-6 text-center text-sm text-muted">该角色没有默认职责。</div>
              ) : null}
              <CandidateDeletionPermissionNote />
              <details
                className="mt-4 overflow-hidden rounded-md border border-slate-200"
                open={showPersonalOverrides}
                onToggle={(event) => setShowPersonalOverrides(event.currentTarget.open)}
              >
                <summary className="cursor-pointer bg-slate-50/70 px-4 py-3 text-sm font-semibold text-slate-700">
                  个人例外（{Object.keys(userDialog.form.responsibilityOverrides ?? {}).length} 项）
                  <span className="ml-2 text-xs font-normal text-muted">仅在与角色默认不同的时候调整</span>
                </summary>
                <div className="divide-y divide-slate-200 border-t border-slate-200">
                  {responsibilityCatalog.map((responsibility) => {
                    const scopeRestricted = responsibility.requiredBusinessScope === "organization"
                      && selectedUserRole?.businessScope !== "organization";
                    const defaultEffect = responsibilityDefaultEffect(selectedUserRole, responsibility.code);
                    const overrideEffect = userDialog.form.responsibilityOverrides?.[responsibility.code];
                    const effect = scopeRestricted ? "deny" : (overrideEffect ?? defaultEffect);
                    return (
                      <div
                        key={responsibility.code}
                        className={`grid min-h-[68px] gap-3 border-l-4 px-4 py-3 sm:grid-cols-[minmax(0,1fr)_200px] sm:items-center ${
                          scopeRestricted
                            ? "border-l-slate-300 bg-slate-50"
                            : overrideEffect === "allow"
                              ? "border-l-emerald-500 bg-emerald-50/30"
                              : overrideEffect === "deny"
                                ? "border-l-rose-500 bg-rose-50/30"
                                : "border-l-transparent bg-white"
                        }`}
                      >
                        <span className="min-w-0">
                          <span className="block text-sm font-semibold text-slate-800">{responsibility.label}</span>
                          <span className={`mt-1 inline-flex rounded px-2 py-0.5 text-[11px] font-semibold leading-4 ${
                            overrideEffect === "allow"
                              ? "border border-emerald-200 bg-emerald-50 text-emerald-700"
                              : overrideEffect === "deny"
                                ? "border border-rose-200 bg-rose-50 text-rose-700"
                                : "border border-slate-200 bg-slate-50 text-slate-500"
                          }`}>
                            {overrideEffect
                              ? `账号单独${overrideEffect === "allow" ? "允许" : "禁止"}`
                              : `角色默认：${defaultEffect === "allow" ? "允许" : "禁止"}`}
                          </span>
                          {scopeRestricted ? (
                            <span className="block text-xs text-amber-700">仅全公司范围角色可授予</span>
                          ) : null}
                        </span>
                        <div className="grid h-9 grid-cols-2 overflow-hidden rounded-md border border-slate-200 bg-white">
                          {(["allow", "deny"] as PermissionEffect[]).map((nextEffect) => (
                            <button
                              key={nextEffect}
                              type="button"
                              disabled={scopeRestricted}
                              className={`text-xs font-semibold transition disabled:cursor-not-allowed disabled:opacity-50 ${
                                nextEffect === "allow" ? "border-r border-slate-200" : ""
                              } ${effect === nextEffect
                                ? nextEffect === "allow"
                                  ? "bg-emerald-50 text-emerald-700"
                                  : "bg-rose-50 text-rose-700"
                                : "bg-white text-slate-500 hover:bg-slate-50"
                              }`}
                              onClick={() => {
                              const next = { ...userDialog.form.responsibilityOverrides };
                              if (nextEffect === defaultEffect) delete next[responsibility.code];
                              else next[responsibility.code] = nextEffect;
                              setUserDialog({
                                ...userDialog,
                                form: { ...userDialog.form, responsibilityOverrides: next }
                              });
                              }}
                            >
                              {nextEffect === "allow" ? "允许" : "禁止"}
                            </button>
                          ))}
                        </div>
                      </div>
                    );
                  })}
                </div>
                <div className="flex justify-end border-t border-slate-200 px-3 py-2">
                  <Button
                    className="h-8 px-2.5 text-xs"
                    type="button"
                    onClick={() => setUserDialog({
                      ...userDialog,
                      form: {
                        ...userDialog.form,
                        responsibilityOverrides: {}
                      }
                    })}
                  >
                    清除全部个人例外
                  </Button>
                </div>
              </details>
            </div>
            {userFormError ? (
              <p className="text-sm text-rose-700" role="alert">{userFormError}</p>
            ) : null}
            <div className="flex flex-wrap gap-5 rounded-md border border-line bg-slate-50 px-3 py-3 text-sm">
              <label
                className={`flex items-center gap-2 ${
                  selectedUserRole?.businessScope === "organization"
                    ? "text-slate-800"
                    : "text-slate-400"
                }`}
                title={selectedUserRole?.businessScope === "organization"
                  ? "组织范围角色可设置系统管理员"
                  : "需先选择全公司数据范围角色"}
              >
                <input
                  type="checkbox"
                  checked={userDialog.form.isSystemAdmin}
                  disabled={selectedUserRole?.businessScope !== "organization"}
                  onChange={(event) => setUserDialog({
                    ...userDialog,
                    form: { ...userDialog.form, isSystemAdmin: event.target.checked }
                  })}
                />
                系统管理员
              </label>
              {userDialog.userId ? (
                <label className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={userDialog.form.isActive ?? true}
                    onChange={(event) => setUserDialog({
                      ...userDialog,
                      form: { ...userDialog.form, isActive: event.target.checked }
                    })}
                  />
                  启用账号
                </label>
              ) : null}
              <p className="basis-full text-xs leading-5 text-muted">
                {selectedUserRole?.businessScope === "organization"
                  ? "系统管理员只管理账号、组织、后台任务和操作日志，不自动获得招聘业务权限。"
                  : "系统管理员必须使用全公司数据范围角色；请选择组织范围角色后再启用。"}
              </p>
            </div>
            <div className="flex justify-end gap-2">
              <Button type="button" onClick={() => setUserDialog(undefined)}>取消</Button>
              <Button type="submit" variant="primary" disabled={savingUser}>
                <Save size={16} />
                {savingUser ? "保存中…" : "保存"}
              </Button>
            </div>
          </form>
        </Dialog>
      ) : null}

      {passwordUser ? (
        <Dialog title={`重置密码 · ${passwordUser.displayName}`} onClose={() => setPasswordUser(undefined)}>
          <form className="space-y-4" onSubmit={submitPasswordReset}>
            <AdminField label="密码">
              <PasswordInput
                name="resetPassword"
                value={resetPassword}
                onChange={setResetPassword}
              />
            </AdminField>
            <AdminField label="确认密码">
              <PasswordInput
                name="confirmResetPassword"
                value={confirmResetPassword}
                onChange={setConfirmResetPassword}
              />
            </AdminField>
            {passwordFormError ? (
              <p className="text-sm text-rose-700" role="alert">{passwordFormError}</p>
            ) : null}
            <div className="flex justify-end gap-2">
              <Button type="button" onClick={() => setPasswordUser(undefined)}>取消</Button>
              <Button type="submit" variant="primary" disabled={resettingPassword}>
                <KeyRound size={16} />
                {resettingPassword ? "重置中…" : "确认重置"}
              </Button>
            </div>
          </form>
        </Dialog>
      ) : null}
      {hardDeleteTarget ? (
        <Dialog title="彻底删除申请" onClose={() => !hardDeleting && setHardDeleteTarget(undefined)}>
          <p className="text-sm leading-6 text-slate-700">
            此操作将永久删除该申请的简历、评分、面评、任务和工作流数据，无法恢复。
          </p>
          <div className="mt-3 rounded-md bg-rose-50 px-3 py-2 text-sm text-rose-800">
            {hardDeleteTarget.candidateName} · {hardDeleteTarget.jobTitle}
          </div>
          <label className="mt-4 block text-sm font-medium text-slate-700">
            输入候选人姓名“{hardDeleteTarget.candidateName}”确认
            <input
              className="admin-input mt-2"
              value={hardDeleteConfirm}
              onChange={(event) => setHardDeleteConfirm(event.target.value)}
            />
          </label>
          <div className="mt-4 flex justify-end gap-2">
            <Button disabled={hardDeleting} onClick={() => setHardDeleteTarget(undefined)}>取消</Button>
            <Button
              variant="danger"
              disabled={hardDeleting || hardDeleteConfirm.trim() !== hardDeleteTarget.candidateName}
              onClick={() => void confirmHardDelete()}
            >
              {hardDeleting ? "正在彻底删除" : "永久删除"}
            </Button>
          </div>
        </Dialog>
      ) : null}
    </>
  );
}

function changeRoleResponsibility(
  responsibilities: ResponsibilityBundleCode[],
  code: ResponsibilityBundleCode,
  enabled: boolean
): ResponsibilityBundleCode[] {
  const next = new Set(responsibilities);
  enabled ? next.add(code) : next.delete(code);
  return [...next];
}

function RoleManagementPanel({
  roles,
  onCreate,
  onEdit,
  onDelete
}: {
  roles: AdminRoleView[];
  onCreate: () => void;
  onEdit: (role: AdminRoleView) => void;
  onDelete: (role: AdminRoleView) => void;
}) {
  return (
    <Section
      title="角色管理"
      description="角色由数据范围和职责组成，账号选择角色后自动继承。"
      action={<Button type="button" variant="primary" onClick={onCreate}><Plus size={16} />新增角色</Button>}
    >
      <div className="overflow-x-auto rounded-md border border-line">
        <table className="data-table w-full min-w-[760px] border-collapse text-left text-sm">
          <thead className="bg-slate-50 text-xs text-muted">
            <tr>
              <th className="px-3 py-2.5 font-medium">角色</th>
              <th className="px-3 py-2.5 font-medium">数据范围</th>
              <th className="px-3 py-2.5 font-medium">职责 / 账号</th>
              <th className="px-3 py-2.5 font-medium">状态</th>
              <th className="data-table-action px-3 py-2.5">操作</th>
            </tr>
          </thead>
          <tbody>
            {roles.map((role) => (
              <tr key={role.roleId} className="border-t border-line bg-white hover:bg-slate-50/70">
                <td className="px-3 py-3">
                  <div className="font-medium text-ink">{role.name}</div>
                  <div className="text-xs text-muted">{role.isSystem ? "内置角色" : "自定义角色"}</div>
                </td>
                <td className="px-3 py-3">{role.businessScope === "organization" ? "全公司" : "本部门"}</td>
                <td className="px-3 py-3 text-muted">
                  {role.responsibilityBundles.length} 项职责 · {role.userCount} 个账号
                </td>
                <td className="px-3 py-3">
                  <Badge className={role.isActive ? "border-emerald-200 bg-emerald-50 text-emerald-700" : "border-slate-200 bg-slate-100 text-slate-600"}>
                    {role.isActive ? "启用" : "停用"}
                  </Badge>
                </td>
                <td className="data-table-action px-3 py-3">
<RowActionMenu ariaLabel={`${role.name} 的操作`} items={[{ label: "编辑角色", onSelect: () => onEdit(role) }, ...(!role.isSystem ? [{ label: "删除角色", destructive: true, disabled: role.userCount > 0, onSelect: () => onDelete(role) }] : [])]} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Section>
  );
}

function UserManagementPanel({
  users,
  currentUserId,
  onCreate,
  onEdit,
  onDelete,
  onResetPassword
}: {
  users: AdminUserView[];
  currentUserId?: string;
  onCreate: () => void;
  onEdit: (user: AdminUserView) => void;
  onDelete: (user: AdminUserView) => void;
  onResetPassword: (user: AdminUserView) => void;
}) {
  const [filter, setFilter] = useState<"active" | "inactive" | "deleted" | "all">("active");
  const visibleUsers = users.filter((user) => (
    filter === "all" || (filter === "deleted" ? Boolean(user.deletedAt) : filter === "active" ? user.isActive && !user.deletedAt : !user.isActive && !user.deletedAt)
  ));
  return (
    <Section
      title="账号与权限"
      description="管理招聘人员账号、业务角色、部门范围和系统管理员权限；删除账号会保留历史审计。"
      action={<div className="flex gap-2"><select className="admin-input h-9 w-28" value={filter} onChange={(event) => setFilter(event.target.value as typeof filter)}><option value="active">启用</option><option value="inactive">停用</option><option value="deleted">已删除</option><option value="all">全部</option></select><Button type="button" variant="primary" onClick={onCreate}><Plus size={16} />新增账号</Button></div>}
    >
      <div className="overflow-x-auto rounded-md border border-line">
        <table className="data-table w-full min-w-[820px] border-collapse text-left text-sm">
          <thead className="bg-slate-50 text-xs text-muted">
            <tr>
              <th className="px-3 py-2.5 font-medium">账号</th>
              <th className="px-3 py-2.5 font-medium">角色/部门</th>
              <th className="px-3 py-2.5 font-medium">权限</th>
              <th className="px-3 py-2.5 font-medium">状态</th>
              <th className="px-3 py-2.5 font-medium">最后登录</th>
              <th className="data-table-action px-3 py-2.5">操作</th>
            </tr>
          </thead>
          <tbody>
            {visibleUsers.map((user) => (
              <tr key={user.userId} className={`border-t border-line transition ${user.deletedAt ? "bg-slate-50 text-slate-500" : "bg-white hover:bg-slate-50/70"}`}>
                <td className="px-3 py-3">
                  <div className="font-medium text-ink">{user.displayName}</div>
                  <div className="text-xs text-muted">{user.username}</div>
                </td>
                <td className="px-3 py-3">
                  <div>{displayRoleName(user)}</div>
                  <div className="text-xs text-muted">{user.departmentName || "未设置部门"}</div>
                </td>
                <td className="px-3 py-3">
                  <div className="flex flex-wrap gap-1.5">
                    <Badge className="border-slate-200 bg-slate-50 text-slate-700">
                      {user.businessScope === "organization" ? "查看全公司候选人" : "仅查看本部门候选人"}
                    </Badge>
                    {user.isSystemAdmin ? (
                      <Badge className="border-blue-200 bg-blue-50 text-blue-700">系统管理员</Badge>
                    ) : null}
                  </div>
                  <div className="mt-1 text-xs text-muted">
                    已启用 {user.effectiveResponsibilityBundles.length} / {user.responsibilityCatalog.length} 项职责
                  </div>
                </td>
                <td className="px-3 py-3">
                  <Badge className={user.isActive ? "border-emerald-200 bg-emerald-50 text-emerald-700" : "border-slate-200 bg-slate-100 text-slate-600"}>
                    {user.deletedAt ? "已删除" : user.isActive ? "启用" : "停用"}
                  </Badge>
                  {user.deletedAt ? <div className="mt-1 text-xs text-muted">删除时间：{formatDateTime(user.deletedAt)}{user.deletedByName ? ` · 操作人：${user.deletedByName}` : ""}</div> : null}
                  {user.userId === currentUserId ? <span className="ml-2 text-xs text-muted">当前账号</span> : null}
                </td>
                <td className="px-3 py-3 text-xs text-muted">{formatDateTime(user.lastLoginAt)}</td>
                <td className="data-table-action px-3 py-3">
{!user.deletedAt ? <RowActionMenu ariaLabel={`${user.displayName} 的操作`} items={[{ label: "编辑账号", onSelect: () => onEdit(user) }, { label: "重置密码", onSelect: () => onResetPassword(user) }, ...(user.userId !== currentUserId ? [{ label: "删除账号", destructive: true, onSelect: () => onDelete(user) }] : [])]} /> : <span className="text-xs text-muted">仅保留历史记录</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Section>
  );
}

function OrganizationManagementPanel({ users, departments, jobs, onChanged, onError, onMessage }: { users: AdminUserView[]; departments: AdminDepartmentView[]; jobs: AdminJobView[]; onChanged: () => Promise<void>; onError: (value: string) => void; onMessage: (value: string) => void; }) {
  const [newDepartmentName, setNewDepartmentName] = useState("");
  const [departmentNames, setDepartmentNames] = useState<Record<string, string>>({});
  const [jobDrafts, setJobDrafts] = useState<Record<string, AdminJobView>>({});
  const [selectedJobIds, setSelectedJobIds] = useState<Set<string>>(new Set());
  const [bulkDeleting, setBulkDeleting] = useState(false);
  const [departmentFilter, setDepartmentFilter] = useState<"normal" | "deleted" | "all">("normal");
  const [jobFilter, setJobFilter] = useState<"setup_pending" | "open" | "closed" | "all">("all");
  useEffect(() => { setDepartmentNames(Object.fromEntries(departments.map((item) => [item.departmentId, item.name]))); setJobDrafts(Object.fromEntries(jobs.map((item) => [item.jobId, { ...item }]))); setSelectedJobIds((current) => new Set([...current].filter((id) => jobs.some((job) => job.jobId === id && job.lifecycleStatus !== "deleted")))); }, [departments, jobs]);
  const { createDepartment, updateDepartment, updateJob, deleteDepartment, deleteJob, deleteJobs } = useAdminPanelCommands({ onChanged, onError, onMessage });
  const visibleDepartments = departments.filter((item) => departmentFilter === "all" || item.lifecycleStatus === departmentFilter);
  // 删除岗位后仅保留历史关联，不再出现在岗位人员配置列表。
  const visibleJobs = jobs.filter((item) => item.lifecycleStatus !== "deleted" && (jobFilter === "all" || item.lifecycleStatus === jobFilter));
  const blockedJobs = visibleJobs.filter((job) => job.activeApplicationCount > 0);
  function removeDepartment(department: AdminDepartmentView) {
    const warning = department.activeApplicationCount > 0 ? `该部门有 ${department.activeApplicationCount} 个已进入面试或最终决策的申请，当前不能删除。` : `确认删除“${department.name}”吗？\n\n将同步删除所属岗位；初步筛选及更早阶段的申请将标记为“招聘取消”，历史记录仍可查询。`;
    if (!window.confirm(warning)) return;
    void deleteDepartment(department.departmentId);
  }
  function removeJob(job: AdminJobView) {
    if (job.activeApplicationCount > 0) {
      window.alert(`该岗位有 ${job.activeApplicationCount} 个申请已进入面试或最终决策，当前不能删除。请先在招聘流程中完成这些申请。`);
      return;
    }
    if (!window.confirm(`确认删除岗位“${job.title}”吗？\n\n初步筛选及更早阶段的申请将标记为“招聘取消”，历史记录仍可查询。`)) return;
    void deleteJob(job.jobId);
  }
  const selectableJobs = visibleJobs.filter((job) => job.lifecycleStatus !== "deleted");
  const allVisibleSelected = selectableJobs.length > 0 && selectableJobs.every((job) => selectedJobIds.has(job.jobId));
  function toggleJob(jobId: string, checked: boolean) {
    setSelectedJobIds((current) => { const next = new Set(current); if (checked) next.add(jobId); else next.delete(jobId); return next; });
  }
  function toggleAllVisibleJobs(checked: boolean) {
    setSelectedJobIds((current) => { const next = new Set(current); selectableJobs.forEach((job) => checked ? next.add(job.jobId) : next.delete(job.jobId)); return next; });
  }
  async function removeSelectedJobs() {
    const selected = visibleJobs.filter((job) => selectedJobIds.has(job.jobId) && job.lifecycleStatus !== "deleted");
    if (!selected.length || bulkDeleting) return;
    const blocked = selected.filter((job) => job.activeApplicationCount > 0);
    const deletable = selected.filter((job) => job.activeApplicationCount === 0);
    if (!deletable.length) {
      window.alert(`所选岗位均有申请处于面试或最终决策阶段，当前不能删除。请先在招聘流程中完成这些申请。`);
      return;
    }
    const warning = blocked.length
      ? `${blocked.length} 个岗位有已进入面试或最终决策的申请，将跳过；确认删除其余 ${deletable.length} 个岗位吗？`
      : `确认删除选中的 ${selected.length} 个岗位吗？\n\n初步筛选及更早阶段的申请将标记为“招聘取消”，历史记录仍可查询。`;
    if (!window.confirm(warning)) return;
    setBulkDeleting(true);
    try {
      const result = await deleteJobs(deletable.map((job) => job.jobId));
      if (result) setSelectedJobIds((current) => { const next = new Set(current); result.succeeded.forEach((jobId) => next.delete(jobId)); return next; });
    } finally {
      setBulkDeleting(false);
    }
  }

  function saveJob(job: AdminJobView, draft: AdminJobView) {
    // PATCH 只发送本次编辑过的字段。负责人未配置的岗位不应因为“原样回传状态”而
    // 被错误地视为一次非法状态转换。
    const changes: { status?: AdminJobView["status"]; headcount?: number; hiringManagerId?: string; departmentRecruiterId?: string } = {};
    if (draft.status !== job.status) changes.status = draft.status;
    if ((draft.headcount ?? 1) !== (job.headcount ?? 1)) changes.headcount = draft.headcount ?? 1;
    if ((draft.hiringManagerId ?? "") !== (job.hiringManagerId ?? "")) changes.hiringManagerId = draft.hiringManagerId ?? "";
    if ((draft.departmentRecruiterId ?? "") !== (job.departmentRecruiterId ?? "")) changes.departmentRecruiterId = draft.departmentRecruiterId ?? "";
    if (!Object.keys(changes).length) { onMessage("岗位配置没有变化。"); return; }
    void updateJob(job.jobId, changes);
  }
  return <div className="space-y-4">
    <Section title="部门管理" description="删除部门会同步删除所属岗位；已进入面试或最终决策的申请会阻止删除。">
      <form className="mb-4 flex flex-col gap-2 sm:flex-row sm:items-center" onSubmit={(event) => { event.preventDefault(); if (!newDepartmentName.trim()) return; void createDepartment(newDepartmentName.trim()).then(() => setNewDepartmentName("")); }}>
        <input className="admin-input sm:w-72 sm:flex-none" placeholder="新部门名称" value={newDepartmentName} onChange={(event) => setNewDepartmentName(event.target.value)} />
        <Button type="submit" variant="primary" className="shrink-0 whitespace-nowrap"><Plus size={16} />新增部门</Button>
        <select className="admin-input sm:ml-auto sm:w-32" value={departmentFilter} onChange={(event) => setDepartmentFilter(event.target.value as typeof departmentFilter)}><option value="normal">正常</option><option value="deleted">已删除</option><option value="all">全部</option></select>
      </form>
      <div className="space-y-2">{visibleDepartments.map((department) => {
        const deleted = department.lifecycleStatus === "deleted";
        return <div key={department.departmentId} className={`grid items-center gap-2 rounded-md border p-3 md:grid-cols-[minmax(180px,300px)_repeat(4,minmax(78px,1fr))_minmax(150px,1fr)] ${deleted ? "border-slate-200 bg-slate-50 text-slate-500" : "border-line bg-white"}`}>
          <div><input className="admin-input w-full" disabled={deleted} value={departmentNames[department.departmentId] ?? department.name} onChange={(event) => setDepartmentNames((current) => ({ ...current, [department.departmentId]: event.target.value }))} />{deleted ? <p className="mt-1 text-xs text-muted">已删除 · {department.deletedAt ? new Date(department.deletedAt).toLocaleString("zh-CN") : ""}{department.deletedByName ? ` · ${department.deletedByName}` : ""}</p> : null}</div>
          <span className="text-xs">启用账号 {department.activeUserCount}</span><span className="text-xs">主管 {department.managerCount}</span><span className="text-xs">招聘人 {department.recruiterCount}</span><span className="text-xs">进行中申请 {department.activeApplicationCount}</span>
          <div className="flex gap-2 md:justify-end">{!deleted ? <><Button className="h-8 px-2.5 text-xs" type="button" onClick={() => void updateDepartment(department.departmentId, { name: departmentNames[department.departmentId] ?? department.name }, "部门名称已更新。")}>保存</Button><Button className="h-8 px-2.5 text-xs" type="button" variant="danger" onClick={() => removeDepartment(department)}><Trash2 size={14} />删除</Button></> : <span className="text-xs text-muted">仅保留历史记录</span>}</div>
        </div>;
      })}</div>
    </Section>
    <Section title="岗位人员配置" description="有申请已进入面试或最终决策时，岗位暂不可删除；初步筛选及更早阶段的岗位可删除，相关申请将标记为“招聘取消”。">
      {blockedJobs.length ? (
        <div role="note" className="mb-3 flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2.5 text-amber-900">
          <LockKeyhole className="mt-0.5 shrink-0" size={16} />
          <div className="min-w-0 text-sm">
            <p className="font-medium">以下岗位暂不可删除</p>
            <p className="mt-1 text-xs leading-5">{blockedJobs.map((job) => `${job.title}（${job.activeApplicationCount} 个申请）`).join("、")}。这些申请已进入面试或最终决策，请先在招聘流程中完成处理。</p>
          </div>
        </div>
      ) : null}
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2"><div className="flex items-center gap-2"><label className="inline-flex items-center gap-2 text-sm text-muted"><input type="checkbox" checked={allVisibleSelected} onChange={(event) => toggleAllVisibleJobs(event.target.checked)} disabled={!selectableJobs.length || bulkDeleting} />全选当前列表</label>{selectedJobIds.size ? <span className="text-sm text-muted">已选 {selectedJobIds.size} 个</span> : null}</div><div className="flex items-center gap-2"><Button type="button" variant="danger" disabled={!selectedJobIds.size || bulkDeleting} onClick={() => void removeSelectedJobs()}><Trash2 size={14} />{bulkDeleting ? "删除中" : "批量删除"}</Button><select className="admin-input w-32" value={jobFilter} onChange={(event) => setJobFilter(event.target.value as typeof jobFilter)}><option value="all">全部</option><option value="setup_pending">负责人未配置</option><option value="open">开放</option><option value="closed">关闭</option></select></div></div>
      <div className="overflow-x-auto rounded-md border border-line"><table className="data-table w-full min-w-[1180px] border-collapse text-left text-sm"><thead className="bg-slate-50 text-xs text-muted"><tr><th className="w-12 px-3 py-2.5 font-medium">选择</th><th className="px-3 py-2.5 font-medium">岗位</th><th className="px-3 py-2.5 font-medium">部门</th><th className="data-table-number px-3 py-2.5">招聘人数</th><th className="px-3 py-2.5 font-medium">部门主管</th><th className="px-3 py-2.5 font-medium">部门招聘人</th><th className="px-3 py-2.5 font-medium">状态</th><th className="data-table-action px-3 py-2.5">操作</th></tr></thead><tbody>{visibleJobs.map((job) => {
        const draft = jobDrafts[job.jobId] ?? job; const deleted = job.lifecycleStatus === "deleted";
        const managers = users.filter((user) => user.isActive && user.roleId === "department_manager" && user.departmentId === job.departmentId);
        const recruiters = users.filter((user) => user.isActive && user.effectivePermissions.includes("first_interview.manage") && user.departmentId === job.departmentId);
        return <tr key={job.jobId} className={`border-t border-line ${deleted ? "bg-slate-50 text-slate-500" : "bg-white hover:bg-slate-50/70"}`}><td className="px-3 py-3"><input type="checkbox" checked={selectedJobIds.has(job.jobId)} onChange={(event) => toggleJob(job.jobId, event.target.checked)} disabled={deleted || bulkDeleting} aria-label={`选择岗位 ${job.title}`} /></td><td className="px-3 py-3 font-medium">{job.title}{deleted ? <div className="mt-1 text-xs text-muted">已删除 · {job.deletedAt ? new Date(job.deletedAt).toLocaleString("zh-CN") : ""}</div> : null}</td><td className="px-3 py-3">{job.departmentName}</td><td className="data-table-number px-3 py-3"><input className="h-9 w-24 rounded-md border border-line px-2" disabled={deleted} type="number" min={1} value={draft.headcount ?? 1} onChange={(event) => setJobDrafts((current) => ({ ...current, [job.jobId]: { ...draft, headcount: Number(event.target.value) } }))} /></td><td className="px-3 py-3"><select className="h-9 min-w-40 rounded-md border border-line bg-white px-2" disabled={deleted} value={draft.hiringManagerId ?? ""} onChange={(event) => setJobDrafts((current) => ({ ...current, [job.jobId]: { ...draft, hiringManagerId: event.target.value } }))}><option value="">{managers.length ? "未配置" : "本部门暂无可选主管"}</option>{managers.map((manager) => <option key={manager.userId} value={manager.userId}>{manager.displayName}</option>)}</select></td><td className="px-3 py-3"><select className="h-9 min-w-40 rounded-md border border-line bg-white px-2" disabled={deleted} value={draft.departmentRecruiterId ?? ""} onChange={(event) => setJobDrafts((current) => ({ ...current, [job.jobId]: { ...draft, departmentRecruiterId: event.target.value } }))}><option value="">{recruiters.length ? "未配置" : "本部门暂无可选招聘人"}</option>{recruiters.map((recruiter) => <option key={recruiter.userId} value={recruiter.userId}>{recruiter.displayName}</option>)}</select></td><td className="px-3 py-3">{deleted ? <span className="text-xs">已删除</span> : <select className="h-9 rounded-md border border-line bg-white px-2" value={draft.status} onChange={(event) => setJobDrafts((current) => ({ ...current, [job.jobId]: { ...draft, status: event.target.value as AdminJobView["status"] } }))}>{draft.status === "setup_pending" ? <option value="setup_pending">负责人未配置</option> : null}<option value="open">开放</option>{draft.status !== "setup_pending" ? <option value="closed">关闭</option> : null}</select>}</td><td className="data-table-action px-3 py-3">{deleted ? <span className="text-xs text-muted">仅保留历史记录</span> : <RowActionMenu ariaLabel={`${job.title} 的操作`} items={[{ label: "保存配置", onSelect: () => saveJob(job, draft) }, { label: "删除岗位", destructive: true, onSelect: () => removeJob(job) }]} />}</td></tr>;
      })}</tbody></table></div>
    </Section>
  </div>;
}
type HardScreeningCatalogOption = AdminHardScreeningCatalogCriterion;

const bindingOptions = [
  { value: "highest_degree", label: "最高学历", mode: "select" },
  { value: "highest_education_status", label: "最高学历状态", mode: "select" },
  { value: "highest_education_graduation_year", label: "最高学历毕业年份", mode: "number" },
  { value: "relevant_experience_years", label: "工作年限", mode: "number" },
  { value: "project_experience_semantic", label: "项目经历", mode: "text" }, { value: "skills_semantic", label: "专业技能", mode: "text" },
  { value: "certifications_semantic", label: "证书/资质", mode: "text" }, { value: "full_resume_semantic", label: "简历全文", mode: "text" }
] as const;

function defaultBinding(mode: HardScreeningCatalogOption["valueMode"]): string {
  return bindingOptions.find((item) => item.mode === mode)?.value ?? "full_resume_semantic";
}
function valueModeLabel(mode: HardScreeningCatalogOption["valueMode"]): string { return { select: "下拉选择", number: "数值阈值", text: "自由文本" }[mode]; }

export function HardScreeningOptionManagement() {
  const { token } = useAuth();
  const [options, setOptions] = useState<HardScreeningCatalogOption[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [valueDraft, setValueDraft] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const selected = options.find((item) => item.criterionId === selectedId);
  const load = async () => { if (!token) return; const items = await getAdminHardScreeningCriteria(token); setOptions(items); setSelectedId((current) => items.some((item) => item.criterionId === current) ? current : items[0]?.criterionId ?? ""); };
  useEffect(() => { void load().catch((reason) => setError(reason instanceof Error ? reason.message : "硬筛选项加载失败")); }, [token]);
  const update = (id: string, patch: Partial<HardScreeningCatalogOption>) => setOptions((current) => current.map((item) => item.criterionId === id ? { ...item, ...patch } : item));
  const addValue = () => { if (!selected || selected.valueMode !== "select") return; const value = valueDraft.trim(); if (!value || selected.allowedValues.includes(value)) return; update(selected.criterionId, { allowedValues: [...selected.allowedValues, value] }); setValueDraft(""); };
  const save = async () => { if (!token) return; setSaving(true); setError(""); try { const saved = await Promise.all(options.map((item) => updateAdminHardScreeningCriterion(token, item.criterionId, { name: item.name, valueMode: item.valueMode, allowedValues: item.allowedValues, evaluationBinding: item.evaluationBinding, enabled: item.enabled, sortOrder: item.sortOrder }))); setOptions(saved); setMessage("硬筛选项目录已保存。岗位策略仅能使用已启用项目。"); } catch (reason) { setError(reason instanceof Error ? reason.message : "硬筛选项保存失败"); } finally { setSaving(false); } };
  const add = async () => { if (!token) return; try { const item = await createAdminHardScreeningCriterion(token, { name: "新硬筛项", valueMode: "text", allowedValues: [], evaluationBinding: "full_resume_semantic", enabled: true, sortOrder: options.length + 1 }); setOptions((current) => [...current, item]); setSelectedId(item.criterionId); setMessage("已新增硬筛项，请补充名称和核验来源后保存。"); } catch (reason) { setError(reason instanceof Error ? reason.message : "新增硬筛项失败"); } };
  const remove = async (id: string) => { if (!token || !window.confirm("删除后不会影响历史岗位策略和既有硬筛结果。确认删除吗？")) return; try { await deleteAdminHardScreeningCriterion(token, id); await load(); setMessage("硬筛选项已删除。"); } catch (reason) { setError(reason instanceof Error ? reason.message : "删除硬筛选项失败"); } };
  const reset = async () => { if (!token || !window.confirm("恢复默认项会重置内置硬筛项的名称、取值和启用状态，不影响历史岗位策略。确认继续吗？")) return; try { const items = await resetAdminHardScreeningCriteria(token); setOptions(items); setSelectedId(items[0]?.criterionId ?? ""); setMessage("已恢复默认硬筛选项。"); } catch (reason) { setError(reason instanceof Error ? reason.message : "恢复默认项失败"); } };
  return <Section title="硬筛选项管理" description="维护岗位可选的硬筛条件。停用后不再用于新岗位配置，已保存的历史策略保持不变。" action={<div className="flex gap-2"><Button type="button" onClick={() => void reset()}>恢复初始设置</Button><Button type="button" variant="primary" onClick={() => void add()}><Plus size={16} />新增选项</Button></div>}>
    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">{options.map((option) => { const active = option.criterionId === selectedId; return <article key={option.criterionId} role="button" tabIndex={0} onClick={() => { setSelectedId(option.criterionId); setValueDraft(""); }} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); setSelectedId(option.criterionId); } }} className={`cursor-pointer rounded-lg border p-4 transition ${active ? "border-blue-400 bg-blue-50/40 ring-1 ring-blue-200" : option.enabled ? "border-line bg-white hover:border-slate-300 hover:shadow-sm" : "border-slate-200 bg-slate-50 opacity-75"}`}><div className="flex items-start justify-between gap-3"><div className="min-w-0"><h3 className="truncate font-semibold text-ink">{option.name}</h3><p className="mt-1 text-xs text-muted">{option.enabled ? valueModeLabel(option.valueMode) : "已停用，不会出现在岗位配置中"}</p></div><button type="button" aria-pressed={option.enabled} aria-label={`${option.name}：${option.enabled ? "已启用" : "已停用"}`} className="flex shrink-0 items-center gap-2 text-xs" onClick={(event) => { event.stopPropagation(); update(option.criterionId, { enabled: !option.enabled }); }}><span className={`relative h-5 w-9 rounded-full ${option.enabled ? "bg-emerald-500" : "bg-slate-300"}`}><span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow-sm transition ${option.enabled ? "left-4" : "left-0.5"}`} /></span><span className={option.enabled ? "font-medium text-emerald-700" : "font-medium text-slate-500"}>{option.enabled ? "已启用" : "已停用"}</span></button></div><div className="mt-4 min-h-7">{option.valueMode === "select" ? <div className="flex flex-wrap gap-1.5">{option.allowedValues.slice(0, 3).map((value) => <span key={value} className="rounded-full bg-slate-100 px-2 py-1 text-xs text-slate-600">{value}</span>)}{option.allowedValues.length > 3 ? <span className="rounded-full bg-slate-100 px-2 py-1 text-xs text-slate-600">+{option.allowedValues.length - 3}</span> : null}</div> : <span className="text-xs text-muted">{option.valueMode === "number" ? "数值范围由岗位填写" : "岗位填写文本条件"}</span>}</div><div className="mt-4 flex justify-between border-t border-line pt-3"><Button className="h-8 px-2.5 text-xs" type="button" onClick={(event) => { event.stopPropagation(); setSelectedId(option.criterionId); }}>编辑</Button><Button className="h-8 px-2.5 text-xs" type="button" variant="danger" onClick={(event) => { event.stopPropagation(); void remove(option.criterionId); }}><Trash2 size={14} />删除</Button></div></article>; })}</div>
    {selected ? <section className="mt-5 rounded-lg border border-line bg-slate-50 p-5"><div className="flex items-start justify-between border-b border-line pb-4"><div><p className="text-xs font-medium text-blue-700">正在编辑</p><h3 className="mt-1 text-base font-semibold text-ink">{selected.name}</h3></div><Button type="button" variant="danger" onClick={() => void remove(selected.criterionId)}><Trash2 size={15} />删除此选项</Button></div><div className="mt-5 grid gap-4 lg:grid-cols-3"><label className="space-y-1.5 text-sm"><span className="font-medium text-slate-700">选项名称</span><input className="admin-input w-full" value={selected.name} onChange={(event) => update(selected.criterionId, { name: event.target.value })} /></label><label className="space-y-1.5 text-sm"><span className="font-medium text-slate-700">取值方式</span><select className="admin-input w-full" value={selected.valueMode} onChange={(event) => { const mode = event.target.value as HardScreeningCatalogOption["valueMode"]; update(selected.criterionId, { valueMode: mode, allowedValues: [], evaluationBinding: defaultBinding(mode) }); }}><option value="select">下拉选择</option><option value="number">数值阈值</option><option value="text">自由文本</option></select></label><label className="space-y-1.5 text-sm"><span className="font-medium text-slate-700">核验来源</span><select className="admin-input w-full" value={selected.evaluationBinding} onChange={(event) => update(selected.criterionId, { evaluationBinding: event.target.value })}>{bindingOptions.filter((item) => item.mode === selected.valueMode).map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label></div>{selected.valueMode === "select" ? <div className="mt-5"><p className="text-sm font-medium text-slate-700">可选取值</p><div className="mt-2 min-h-14 rounded-md border border-line bg-white p-3">{selected.allowedValues.length ? <div className="flex flex-wrap gap-2">{selected.allowedValues.map((value) => <span key={value} className="inline-flex items-center gap-1.5 rounded-full border border-blue-200 bg-blue-50 px-3 py-1.5 text-sm text-blue-700">{value}<button type="button" aria-label={`删除取值 ${value}`} className="text-blue-500 hover:text-rose-600" onClick={() => update(selected.criterionId, { allowedValues: selected.allowedValues.filter((item) => item !== value) })}>×</button></span>)}</div> : <p className="py-1 text-sm text-muted">暂未配置可选取值。</p>}</div><div className="mt-3 flex max-w-xl gap-2"><input className="admin-input min-w-0 flex-1" placeholder="输入一个可选取值" value={valueDraft} onChange={(event) => setValueDraft(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); addValue(); } }} /><Button type="button" onClick={addValue}><Plus size={16} />添加取值</Button></div></div> : <p className="mt-5 text-sm text-muted">{selected.valueMode === "number" ? "岗位配置时可填写最小值、最大值，任一可留空。" : "岗位配置时填写需要在简历中得到证明的文本条件。"}</p>}</section> : null}
    <div className="mt-5 flex justify-end"><Button type="button" variant="primary" disabled={saving} onClick={() => void save()}><Save size={16} />{saving ? "保存中" : "保存"}</Button></div>{message ? <p className="mt-2 text-sm text-emerald-700">{message}</p> : null}{error ? <p className="mt-2 text-sm text-rose-700">{error}</p> : null}
  </Section>;
}
function WorkflowManagementPanel({
  token,
  summary,
  workflows,
  statusFilter,
  hasMore,
  onLoadMore,
  onRefresh,
  onStatusFilterChange,
  onChanged,
  onError,
  onMessage
}: {
  token: string | null;
  summary: WorkflowSummary;
  workflows: AdminWorkflowView[];
  statusFilter: string;
  hasMore: boolean;
  onLoadMore: () => Promise<void>;
  onRefresh: () => Promise<void>;
  onStatusFilterChange: (status: string) => Promise<void>;
  onChanged: () => Promise<void>;
  onError: (value: string) => void;
  onMessage: (value: string) => void;
}) {
  const [expandedErrorId, setExpandedErrorId] = useState<string>();
  const [selectedWorkflow, setSelectedWorkflow] = useState<AdminWorkflowView>();
  function toggleExactStatus(status: AdminWorkflowView["status"]) {
    void onStatusFilterChange(statusFilter === status ? "" : status);
  }
  const { retryWorkflow } = useAdminPanelCommands({ onChanged, onError, onMessage });


  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        <StatCard label="等待任务" value={summary.pending} tone="amber" selected={statusFilter === "pending"} onClick={() => toggleExactStatus("pending")} />
        <StatCard label="运行任务" value={summary.running} tone="blue" selected={statusFilter === "running"} onClick={() => toggleExactStatus("running")} />
        <StatCard label="待确认" value={summary.blocked} tone="amber" selected={statusFilter === "blocked"} onClick={() => toggleExactStatus("blocked")} />
        <StatCard label="已完成" value={summary.completed} tone="green" selected={statusFilter === "completed"} onClick={() => toggleExactStatus("completed")} />
        <StatCard label="失败任务" value={summary.failed} tone="rose" selected={statusFilter === "failed"} onClick={() => toggleExactStatus("failed")} />
      </div>
      <div className="flex items-center justify-between gap-3">
        <div className="text-sm font-semibold text-ink">任务记录</div>
        <div className="flex items-center gap-2">
          <select value={["attention", "finished"].includes(statusFilter) ? statusFilter : ""} onChange={(event) => void onStatusFilterChange(event.target.value)} className="h-9 rounded-md border border-line bg-white px-3 text-sm">
            <option value="">全部记录</option>
            <option value="attention">需关注</option>
            <option value="finished">已结束</option>
          </select>
          <Button type="button" className="h-9 px-3 text-xs" onClick={() => void onRefresh()}><RefreshCw size={14} />刷新</Button>
        </div>
      </div>
      <Section title="后台任务运行记录" description="每行展示最新运行事件；可从“查看运行轨迹”读取完整步骤历史。">
        <div className="overflow-x-auto rounded-md border border-line">
          <table className="data-table w-full min-w-[1120px] table-fixed border-collapse text-left text-sm">
            <colgroup>
              <col className="w-[18%]" />
              <col className="w-[12%]" />
              <col className="w-[8%]" />
              <col className="w-[15%]" />
              <col className="w-[7%]" />
              <col className="w-[12%]" />
              <col className="w-[16%]" />
              <col className="w-[16%]" />
              <col className="w-[10%]" />
            </colgroup>
            <thead className="bg-slate-50 text-xs text-muted">
              <tr>
              <th className="px-3 py-3">任务类型</th>
                <th className="px-3 py-3">对象</th>
                <th className="px-3 py-3">状态</th>
                <th className="px-3 py-3">当前步骤</th>
                <th className="data-table-number px-3 py-3">尝试</th>
                <th className="px-3 py-3">更新时间</th>
                <th className="px-3 py-3">最新运行事件</th>
                <th className="px-3 py-3">错误</th>
                <th className="data-table-action px-3 py-2.5">操作</th>
              </tr>
            </thead>
            <tbody>
              {workflows.map((workflow) => {
                const expanded = expandedErrorId === workflow.workflowRunId;
                return (
                  <Fragment key={workflow.workflowRunId}>
                    <tr className="border-t border-line bg-white align-top transition hover:bg-slate-50/70">
                      <td className="px-3 py-3">
                        <div className="font-medium">{taskTypeLabels[workflow.workflowType] ?? "后台任务"}</div>
                      </td>
                      <td className="px-3 py-3 text-xs"><div className="truncate">{workflow.applicationId ? "岗位申请" : workflowSubjectLabels[workflow.subjectType ?? ""] ?? "业务对象"}</div></td>
                      <td className="px-3 py-3"><WorkflowStatusBadge status={workflow.status} /></td>
                      <td className="px-3 py-3 text-xs">
                        <div className="font-medium text-slate-700">{workflow.currentStepName ? humanizeWorkflowStep(workflow.currentStepName) : "—"}</div>
                        {workflow.currentStepStatus ? <div className="mt-0.5 text-muted">{workflowStepStatusLabel(workflow.currentStepStatus)}</div> : null}
                        {workflow.nextAttemptAt ? <div className="mt-0.5 text-amber-700">下次：{formatDateTime(workflow.nextAttemptAt)}</div> : null}
                      </td>
                      <td className="data-table-number px-3 py-3">{workflow.attemptCount}/{workflow.maxAttempts}</td>
                      <td className="px-3 py-3 text-xs text-muted">{formatDateTime(workflow.updatedAt)}</td>
                      <td className="px-3 py-3 text-xs leading-5">
                        {workflow.latestEventMessage ? <div className={workflow.latestEventSeverity === "error" ? "text-rose-700" : workflow.latestEventSeverity === "warning" ? "text-amber-800" : "text-slate-700"}>{workflow.latestEventMessage}<div className="mt-0.5 text-muted">{formatDateTime(workflow.latestEventAt)}</div></div> : <span className="text-muted">尚未产生事件</span>}
                      </td>
                      <td className="px-3 py-3 text-xs leading-5">
                        {workflow.errorMessage ? (
                          <>
                            <div className="line-clamp-2 [overflow-wrap:anywhere] text-rose-700">{workflowErrorSummary(workflow.errorMessage)}</div>
                            <button type="button" className="mt-1 font-medium text-blue-700 hover:text-blue-900" onClick={() => setExpandedErrorId(expanded ? undefined : workflow.workflowRunId)}>
                              {expanded ? "收起错误" : "查看错误"}
                            </button>
                          </>
                        ) : <span className="text-muted">—</span>}
                      </td>
                      <td className="data-table-action px-3 py-3">
<RowActionMenu ariaLabel={`${taskTypeLabels[workflow.workflowType] ?? "后台任务"} 的操作`} items={[{ label: "查看运行轨迹", onSelect: () => setSelectedWorkflow(workflow) }, ...(workflow.availableActions.includes("resume") ? [{ label: "继续任务", onSelect: () => void retryWorkflow(workflow.workflowRunId, "resume" as const) }] : []), ...(workflow.availableActions.includes("retry") ? [{ label: "重试任务", onSelect: () => void retryWorkflow(workflow.workflowRunId, "retry" as const) }] : [])]} /></td>
                    </tr>
                    {expanded && workflow.errorMessage ? (
                      <tr className="border-t border-line bg-slate-50/70">
                        <td colSpan={9} className="px-3 py-3">
                          <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted">
                            <span>处理说明</span>
                            <span>任务编号：{workflow.workflowRunId}</span>
                            <span>已尝试 {workflow.attemptCount}/{workflow.maxAttempts} 次</span>
                          </div>
                          <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-all rounded-md border border-rose-100 bg-white p-3 text-xs leading-5 text-rose-800">{workflow.errorMessage}</pre>
                        </td>
                      </tr>
                    ) : null}
                  </Fragment>
                );
              })}
              {workflows.length === 0 ? (
                <tr><td colSpan={9} className="px-3 py-10 text-center text-sm text-muted">当前没有符合条件的后台任务。</td></tr>
              ) : null}
            </tbody>
          </table>
        </div>
        {hasMore ? (
          <div className="mt-3 flex justify-center">
            <Button type="button" variant="secondary" onClick={() => void onLoadMore()}>加载更多任务</Button>
          </div>
        ) : null}
      </Section>
      {selectedWorkflow ? (
        <WorkflowTimelineDialog
          token={token}
          workflow={selectedWorkflow}
          onClose={() => setSelectedWorkflow(undefined)}
        />
      ) : null}
    </div>
  );
}

function WorkflowTimelineDialog({
  token,
  workflow,
  onClose
}: {
  token: string | null;
  workflow: AdminWorkflowView;
  onClose: () => void;
}) {
  const [events, setEvents] = useState<WorkflowExecutionEventView[]>([]);
  const [nextCursor, setNextCursor] = useState<string>();
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");

  const loadEvents = useCallback(async (cursor = "", append = false) => {
    if (!token) return;
    append ? setLoadingMore(true) : setLoading(true);
    setError("");
    try {
      const page = await getAdminWorkflowExecutionEvents(token, workflow.workflowRunId, { cursor });
      setEvents((current) => append ? [...current, ...page.items] : page.items);
      setNextCursor(page.nextCursor);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "任务运行轨迹加载失败");
    } finally {
      append ? setLoadingMore(false) : setLoading(false);
    }
  }, [token, workflow.workflowRunId]);

  useEffect(() => { void loadEvents(); }, [loadEvents]);

  // 仅在管理员打开运行中任务的轨迹时轮询；历史完成任务不会持续发请求。
  useEffect(() => {
    if (!["pending", "running"].includes(workflow.status)) return;
    const intervalId = window.setInterval(() => { void loadEvents(); }, 5000);
    return () => window.clearInterval(intervalId);
  }, [loadEvents, workflow.status]);

  return (
    <Dialog title="任务运行轨迹" onClose={onClose}>
      <div className="space-y-3">
        <div className="rounded-md border border-line bg-slate-50 px-3 py-2 text-xs text-muted">
          <div>{taskTypeLabels[workflow.workflowType] ?? "后台任务"} · {workflow.applicationId ? "岗位申请" : workflowSubjectLabels[workflow.subjectType ?? ""] ?? "业务对象"}</div>
          <div className="mt-1">当前状态：{workflowStepStatusLabel(workflow.status)}{workflow.currentStepName ? ` · ${humanizeWorkflowStep(workflow.currentStepName)}` : ""}</div>
          <details className="mt-2">
            <summary className="cursor-pointer font-medium text-slate-600">技术详情</summary>
            <div className="mt-1 break-all">任务编号：{workflow.workflowRunId}</div>
          </details>
        </div>
        {error ? <div className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</div> : null}
        {loading ? <div className="py-8 text-center text-sm text-muted">正在读取运行轨迹…</div> : null}
        {!loading && events.length === 0 ? <div className="py-8 text-center text-sm text-muted">该任务尚未产生可展示的步骤事件。</div> : null}
        {!loading && events.length > 0 ? (
          <ol className="space-y-3 border-l border-slate-200 pl-4">
            {events.map((event) => (
              <li key={event.executionEventId} className="relative">
                <span className={`absolute -left-[21px] top-1.5 h-3 w-3 rounded-full border-2 border-white ${timelineTone(event.severity)}`} />
                <div className="rounded-md border border-line bg-white px-3 py-2.5 shadow-sm">
                  <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
                    <div className="font-medium text-sm text-ink">{event.stepLabel ?? "流程更新"}</div>
                    <div className="text-xs text-muted">{formatDateTime(event.occurredAt)}</div>
                  </div>
                  <div className="mt-1 text-sm text-slate-700">{event.message}</div>
                  <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted">
                    {event.attemptCount !== undefined ? <span>第 {event.attemptCount} 次尝试</span> : null}
                    {event.pollCount !== undefined ? <span>轮询 {event.pollCount} 次</span> : null}
                    {event.nextAttemptAt ? <span className="text-amber-700">预计继续：{formatDateTime(event.nextAttemptAt)}</span> : null}
                  </div>
                </div>
              </li>
            ))}
          </ol>
        ) : null}
        {nextCursor ? (
          <div className="flex justify-center pt-1">
            <Button type="button" variant="secondary" disabled={loadingMore} onClick={() => void loadEvents(nextCursor, true)}>
              {loadingMore ? "加载中…" : "加载后续事件"}
            </Button>
          </div>
        ) : null}
      </div>
    </Dialog>
  );
}

function humanizeWorkflowStep(value: string): string {
  const labels: Record<string, string> = {
    freeze_resume_sources: "冻结简历来源版本",
    parse_source_document: "解析简历文件",
    structure_resume_profile: "结构化简历信息",
    freeze_job_profile: "冻结岗位能力画像",
    build_scoring_input: "组装评分输入",
    run_scoring_core: "执行初步筛选评分",
    publish_assessment: "发布评估结果",
    route_candidate_jobs: "匹配候选人岗位",
    publish_applications: "创建岗位申请",
    freeze_interview_sources: "冻结面试评估来源",
    parse_interview_record: "解析面试记录",
    publish_interview_assessment: "发布面试后评估"
  };
  return labels[value] ?? "任务处理";
}

function workflowStepStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    pending: "等待执行",
    running: "执行中",
    waiting_external: "等待外部结果",
    retry_wait: "等待重试",
    blocked: "等待确认",
    succeeded: "已完成",
    failed: "失败",
    completed: "已完成",
    cancelled: "已取消"
  };
  return labels[status] ?? "状态更新中";
}

function timelineTone(severity: WorkflowExecutionEventView["severity"]): string {
  return severity === "error"
    ? "bg-rose-500"
    : severity === "warning"
      ? "bg-amber-500"
      : "bg-blue-500";
}
function DeletedApplicationsPanel({
  items,
  cleanupTasks,
  hasMore,
  onDelete,
  onLoadMore,
  onRetryCleanup
}: {
  items: DeletedApplicationView[];
  cleanupTasks: ObjectCleanupTaskView[];
  hasMore: boolean;
  onDelete: (item: DeletedApplicationView) => void;
  onLoadMore: () => Promise<void>;
  onRetryCleanup: (cleanupTaskId: string) => Promise<void>;
}) {
  return (
    <div className="space-y-4">
      {cleanupTasks.length > 0 ? (
        <Section title="待完成的文件清理" description="申请数据已删除；这里只处理尚未清理完成的关联文件。">
          <div className="overflow-x-auto rounded-md border border-line">
            <table className="data-table w-full min-w-[620px] border-collapse text-left text-sm">
              <thead className="bg-slate-50 text-xs font-medium text-slate-500">
                <tr><th className="px-3 py-2.5">原申请编号</th><th className="px-3 py-2.5">状态</th><th className="px-3 py-2.5">待清理文件</th><th className="px-3 py-2.5">最近尝试</th><th className="data-table-action px-3 py-2.5">操作</th></tr>
              </thead>
              <tbody>{cleanupTasks.map((task) => (
                <tr key={task.cleanupTaskId} className="border-t border-line bg-white">
                  <td className="px-3 py-3 text-xs">{task.applicationId}</td>
                  <td className="px-3 py-3"><Badge>{task.status === "failed" ? "清理未完成" : "等待清理"}</Badge></td>
                  <td className="px-3 py-3">{task.pendingObjectCount}</td>
                  <td className="px-3 py-3 text-xs text-muted">{formatDateTime(task.lastAttemptAt)}</td>
                  <td className="data-table-action px-3 py-3"><Button type="button" className="h-8 px-2.5 text-xs" onClick={() => void onRetryCleanup(task.cleanupTaskId)}><RefreshCw size={14} />重试清理</Button></td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        </Section>
      ) : null}
      <Section
        title="已删除申请"
        description="这里只保留已从招聘流程移除的申请；永久删除后无法恢复。"
      >
      <div className="overflow-x-auto rounded-md border border-line">
        <table className="data-table w-full min-w-[760px] border-collapse text-left text-sm">
          <thead className="bg-slate-50 text-xs font-medium text-slate-500">
            <tr>
              <th className="px-3 py-2.5 font-medium">候选人</th>
              <th className="px-3 py-2.5 font-medium">岗位</th>
              <th className="px-3 py-2.5 font-medium">删除时间</th>
              <th className="px-3 py-2.5 font-medium">操作人</th>
              <th className="data-table-action px-3 py-2.5">操作</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.applicationId} className="border-t border-line bg-white transition hover:bg-slate-50/70">
                <td className="px-3 py-3">
                  <div className="font-medium text-ink">{item.candidateName}</div>
                </td>
                <td className="px-3 py-3">{item.jobTitle}</td>
                <td className="px-3 py-3 text-xs text-muted">{formatDateTime(item.deletedAt)}</td>
                <td className="px-3 py-3">{item.deletedBy || "系统"}</td>
                <td className="data-table-action px-3 py-3">
<RowActionMenu ariaLabel={`${item.candidateName} 的操作`} items={[{ label: "彻底删除申请", destructive: true, onSelect: () => onDelete(item) }]} /></td>
              </tr>
            ))}
            {items.length === 0 ? (
              <tr>
                <td className="px-3 py-8 text-center text-sm text-muted" colSpan={5}>暂无已删除申请。</td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
      </Section>
      {hasMore ? <div className="flex justify-center"><Button type="button" variant="secondary" onClick={() => void onLoadMore()}>加载更多已删除申请</Button></div> : null}
    </div>
  );
}

function AuditManagementPanel({ users, events, hasMore, onLoadMore, onRefresh, onSearch }: { users: AdminUserView[]; events: AuditEventView[]; hasMore: boolean; onLoadMore: () => Promise<void>; onRefresh: () => Promise<void>; onSearch: (query: AdminAuditEventQuery) => Promise<void> }) {
  const [query, setQuery] = useState("");
  const [actor, setActor] = useState("all");
  const [category, setCategory] = useState<AuditCategory>("all");
  const [dateRange, setDateRange] = useState<"all" | "7d" | "30d" | "90d">("30d");
  const [filtering, setFiltering] = useState(false);

  const actors = useMemo(() => users
    .map((user) => ({ userId: user.userId, name: user.displayName }))
    .sort((left, right) => left.name.localeCompare(right.name)), [users]);

  async function applyServerFilters() {
    setFiltering(true);
    try {
      const days = dateRange === "all" ? 0 : Number(dateRange.slice(0, -1));
      await onSearch({
        query: query.trim() || undefined,
        category: category === "all" ? undefined : category,
        actorUserId: actor === "all" ? undefined : actor,
        from: days ? new Date(Date.now() - days * 24 * 60 * 60 * 1000).toISOString() : undefined
      });
    } finally {
      setFiltering(false);
    }
  }
  return (
    <Section title="操作日志" description="只记录人工触发的数据和业务状态变更；页面查看、登录和自动任务流转不记录。">
      <div className="mb-3 grid gap-2 md:grid-cols-2 xl:grid-cols-4">
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="搜索操作人、操作或对象"
          className="h-9 rounded-md border border-line bg-white px-3 text-sm outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100"
        />
        <select value={actor} onChange={(event) => setActor(event.target.value)} className="h-9 rounded-md border border-line bg-white px-3 text-sm">
          <option value="all">全部操作人</option>
          {actors.map((item) => <option key={item.userId} value={item.userId}>{item.name}</option>)}
        </select>
        <select value={category} onChange={(event) => setCategory(event.target.value as AuditCategory)} className="h-9 rounded-md border border-line bg-white px-3 text-sm">
          <option value="all">全部操作类型</option>
          <option value="account">账号与权限</option>
          <option value="organization">组织与岗位</option>
          <option value="recruitment">招聘业务</option>
          <option value="task">后台任务</option>
        </select>
        <select value={dateRange} onChange={(event) => setDateRange(event.target.value as typeof dateRange)} className="h-9 rounded-md border border-line bg-white px-3 text-sm">
          <option value="7d">近 7 天</option>
          <option value="30d">近 30 天</option>
          <option value="90d">近 90 天</option>
          <option value="all">全部时间</option>
        </select>
        <Button type="button" className="h-9 justify-center" disabled={filtering} onClick={() => void applyServerFilters()}>
          {filtering ? "查询中…" : "查询"}
        </Button>
      </div>
      <div className="mb-2 flex items-center justify-between gap-3 text-xs text-muted"><span>当前已加载 {events.length} 条操作记录</span><Button type="button" className="h-8 px-2.5 text-xs" onClick={() => void onRefresh()}><RefreshCw size={14} />刷新</Button></div>
      <div className="overflow-x-auto rounded-md border border-line">
        <table className="data-table w-full min-w-[920px] border-collapse text-left text-sm">
          <thead className="bg-slate-50 text-xs text-muted">
            <tr>
              <th className="px-3 py-3">时间</th>
              <th className="px-3 py-3">操作人</th>
              <th className="px-3 py-3">操作</th>
              <th className="px-3 py-3">对象</th>
              <th className="px-3 py-3">说明</th>
            </tr>
          </thead>
          <tbody>
            {events.map((event) => (
                  <tr key={event.auditEventId} className="border-t border-line bg-white align-top transition hover:bg-slate-50/70">
                    <td className="whitespace-nowrap px-3 py-3 text-xs text-muted">{formatDateTime(event.createdAt)}</td>
                    <td className="px-3 py-3">{event.actorName}</td>
                    <td className="px-3 py-3"><Badge>{auditActionLabels[event.action] ?? "其他操作"}</Badge></td>
                    <td className="px-3 py-3 text-xs">
                      <div>{auditTargetLabels[event.targetType] ?? "业务对象"}</div>
                    </td>
                    <td className="px-3 py-3">{event.summary}</td>
                  </tr>
            ))}
            {events.length === 0 ? (
              <tr><td colSpan={5} className="px-3 py-10 text-center text-sm text-muted">没有符合条件的操作记录。</td></tr>
            ) : null}
          </tbody>
        </table>
      </div>
      {hasMore ? (
        <div className="mt-3 flex justify-center">
          <Button type="button" variant="secondary" onClick={() => void onLoadMore()}>加载更多操作记录</Button>
        </div>
      ) : null}
    </Section>
  );
}

function WorkflowStatusBadge({ status }: { status: AdminWorkflowView["status"] }) {
  const styles: Record<string, string> = {
    pending: "border-amber-200 bg-amber-50 text-amber-800",
    running: "border-blue-200 bg-blue-50 text-blue-700",
    blocked: "border-amber-200 bg-amber-50 text-amber-800",
    completed: "border-emerald-200 bg-emerald-50 text-emerald-700",
    failed: "border-rose-200 bg-rose-50 text-rose-700",
    cancelled: "border-slate-200 bg-slate-100 text-slate-600"
  };
  const labels: Record<string, string> = { pending: "等待", running: "运行中", blocked: "待确认", completed: "完成", failed: "失败", cancelled: "已取消" };
  return <Badge className={styles[status] ?? "border-slate-200 bg-slate-100 text-slate-600"}>{labels[status] ?? "状态更新中"}</Badge>;
}

function workflowErrorSummary(message: string): string {
  const normalized = message.replace(/\s+/g, " ").trim();
  const mappings: Array<[RegExp, string]> = [
    [/work_unit_extraction_failed/i, "简历工作单元提取失败"],
    [/resume_project_assessment/i, "简历项目能力评估失败"],
    [/screening_foundation/i, "初步筛选基础数据处理失败"],
    [/document.*(?:parse|extract)|(?:parse|extract).*document/i, "文件内容解析失败"],
    [/timeout|timed out/i, "外部服务响应超时"],
    [/rate.?limit|too many requests|status.?429/i, "模型服务请求频率受限"],
    [/json|schema|validation/i, "模型返回内容格式不符合要求"],
    [/parallel_stage_failed|BatchExecutionError/i, "并行处理阶段失败"]
  ];
  for (const [pattern, label] of mappings) {
    if (pattern.test(normalized)) return label;
  }
  return normalized ? "后台任务执行失败，请展开技术详情查看原因。" : "后台任务执行失败";
}

function PasswordInput({
  name,
  value,
  onChange
}: {
  name: string;
  value: string;
  onChange: (value: string) => void;
}) {
  const [visible, setVisible] = useState(false);
  return (
    <span className="relative block">
      <input
        required
        minLength={8}
        name={name}
        autoComplete="new-password"
        type={visible ? "text" : "password"}
        className="admin-input bg-blue-50 pr-10"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
      <button
        type="button"
        className="absolute inset-y-0 right-0 flex w-10 items-center justify-center text-slate-500"
        aria-label={visible ? "隐藏密码" : "显示密码"}
        title={visible ? "隐藏密码" : "显示密码"}
        onClick={() => setVisible((current) => !current)}
      >
        {visible ? <EyeOff size={17} /> : <Eye size={17} />}
      </button>
    </span>
  );
}

function AdminField({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="space-y-1.5 text-sm">
      <span className="font-medium text-slate-700">{label}</span>
      {children}
    </label>
  );
}

function CandidateDeletionPermissionNote() {
  return (
    <div className="mt-4 flex items-start gap-2.5 rounded-md border border-slate-200 bg-slate-50 px-3.5 py-3 text-xs leading-5 text-slate-600">
      <Info size={17} className="mt-0.5 shrink-0 text-slate-500" />
      <span>
        <strong className="font-semibold text-slate-700">候选人整体删除不属于上述职责。</strong>
        该操作会归档候选人的全部申请并取消进行中的任务，仅系统管理员可以执行。
      </span>
    </div>
  );
}

function Dialog({
  title,
  children,
  onClose,
  wide = false
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  wide?: boolean;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center overflow-hidden bg-slate-950/40 p-3 sm:p-4" role="dialog" aria-modal="true">
      <div className={`flex max-h-[calc(100dvh-1.5rem)] w-full flex-col overflow-hidden rounded-lg bg-white shadow-xl sm:max-h-[calc(100dvh-2rem)] ${wide ? "max-w-4xl" : "max-w-xl"}`}>
        <div className="flex shrink-0 items-center justify-between gap-3 border-b border-line px-5 py-4">
          <h2 className="text-lg font-semibold text-ink">{title}</h2>
          <button type="button" className="shrink-0 rounded-md px-2 py-1 text-sm text-muted hover:bg-slate-100 hover:text-ink" onClick={onClose}>关闭</button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-5">{children}</div>
      </div>
    </div>
  );
}

function formatDateTime(value?: string): string {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { hour12: false });
}
