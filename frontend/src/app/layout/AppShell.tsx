import {
  BarChart3,
  ClipboardList,
  FileSearch,
  FileUp,
  LogOut,
  Settings,
  ShieldCheck,
  SlidersHorizontal,
  Users,
  X
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { useCallback, useEffect, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { displayRoleName, roleDescriptions } from "@/modules/auth/accessPolicy";
import { statusLabels, statusTone } from "@/modules/applications/status";
import { useAuth } from "@/modules/auth/AuthProvider";
import {
  getApplicationList,
  getRecruitmentTimeline,
  type ApplicationListItem,
  type RecruitmentTimelineItem
} from "@/modules/applications/api";
import {
  getNavigationNotificationCounts,
  type NavigationNotificationCounts
} from "@/modules/tasks/notificationsApi";
import { Badge } from "@/shared/ui/Badge";
import {
  hasBusinessPermission,
  type BusinessPermission
} from "@/modules/auth/permissions";

interface NavItem {
  label: string;
  path: string;
  icon: LucideIcon;
  permissions?: BusinessPermission[];
  adminOnly?: boolean;
}

const navItems: NavItem[] = [
  {
    label: "任务中心",
    path: "/",
    icon: ClipboardList
  },
  {
    label: "候选人列表",
    path: "/candidates",
    icon: Users
  },
  {
    label: "岗位管理",
    path: "/jobs",
    icon: FileUp
  },
  {
    label: "部门概览",
    path: "/analytics",
    icon: BarChart3
  },
  {
    label: "招聘配置",
    path: "/recruitment-settings",
    icon: SlidersHorizontal,
    permissions: ["hard_screening.catalog.manage", "interview_guide.manage"]
  },
  {
    label: "系统管理",
    path: "/admin",
    icon: Settings,
    adminOnly: true
  }
];

function extractApplicationId(pathname: string): string | undefined {
  return pathname.match(/applications\/([^/]+)/)?.[1];
}

export function AppShell() {
  const { token, user, logout } = useAuth();
  const [navOpen, setNavOpen] = useState(false);
  const [statusOpen, setStatusOpen] = useState(false);
  const [applicationSummary, setApplicationSummary] = useState<ApplicationListItem>();
  const [timeline, setTimeline] = useState<RecruitmentTimelineItem[]>([]);
  const [notificationCounts, setNotificationCounts] = useState<NavigationNotificationCounts>({
    taskUnreadCount: 0,
    candidateUnreadCount: 0
  });
  const location = useLocation();
  const navigate = useNavigate();
  const applicationId = extractApplicationId(location.pathname);
  const visibleNav = navItems.filter(
    (item) => user && (
      item.adminOnly
        ? user.isSystemAdmin && user.businessScope === "organization"
        : (!item.permissions || item.permissions.some((permission) => hasBusinessPermission(user, permission)))
    )
  );

  const loadNotificationCounts = useCallback(async () => {
    if (!token) return;
    try {
      setNotificationCounts(await getNavigationNotificationCounts(token));
    } catch {
      setNotificationCounts({ taskUnreadCount: 0, candidateUnreadCount: 0 });
    }
  }, [token]);

  useEffect(() => {
    void loadNotificationCounts();
  }, [loadNotificationCounts, location.pathname, navOpen]);

  useEffect(() => {
    const refresh = () => void loadNotificationCounts();
    window.addEventListener("focus", refresh);
    window.addEventListener("navigation-notifications-changed", refresh);
    return () => {
      window.removeEventListener("focus", refresh);
      window.removeEventListener("navigation-notifications-changed", refresh);
    };
  }, [loadNotificationCounts]);

  useEffect(() => {
    if (!token || !applicationId || !statusOpen) {
      setApplicationSummary(undefined);
      setTimeline([]);
      return;
    }
    let cancelled = false;
    void Promise.all([
      getApplicationList(token, { page: 1, pageSize: 1, keyword: applicationId }),
      getRecruitmentTimeline(token, applicationId)
    ])
      .then(([view, timelineView]) => {
        if (!cancelled) {
          setApplicationSummary(view.items.find((item) => item.applicationId === applicationId));
          setTimeline(timelineView.items);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setApplicationSummary(undefined);
          setTimeline([]);
        }
      });
    return () => { cancelled = true; };
  }, [applicationId, statusOpen, token]);

  return (
    <div className="min-h-screen bg-surface text-ink">
      <div className="fixed left-4 top-4 z-50">
        <button
          className={`flex h-10 w-10 items-center justify-center rounded-md shadow-lg transition ${
            navOpen ? "bg-blue-700 text-white" : "bg-blue-600 text-white hover:bg-blue-700"
          }`}
          type="button"
          aria-label={navOpen ? "收起导航" : "展开导航"}
          onClick={() => setNavOpen((open) => !open)}
        >
          <ShieldCheck size={22} aria-hidden="true" />
        </button>
      </div>

      <div className="fixed right-4 top-4 z-50">
        <button
          className={`inline-flex h-8 items-center justify-center gap-2 rounded-md border px-2.5 text-xs font-semibold shadow-lg transition ${
            statusOpen ? "border-blue-600 bg-blue-600 text-white" : "border-line bg-white/95 text-slate-700 hover:bg-slate-50"
          }`}
          type="button"
          onClick={() => setStatusOpen((open) => !open)}
        >
          <FileSearch size={14} aria-hidden="true" />
          当前流程状态
        </button>
      </div>

      {navOpen ? (
        <FloatingShellPanel side="left" title="招聘 AI 工作台" onClose={() => setNavOpen(false)}>
          <div className="space-y-4">
            <nav className="space-y-1">
              {visibleNav.map((item) => {
                const Icon = item.icon;
                const unreadCount = item.path === "/"
                  ? notificationCounts.taskUnreadCount
                  : item.path === "/candidates"
                    ? notificationCounts.candidateUnreadCount
                    : 0;
                return (
                  <NavLink
                    key={item.path}
                    to={item.path}
                    onClick={() => setNavOpen(false)}
                    className={({ isActive }) =>
                      `flex items-center gap-3 rounded-md px-3 py-2.5 text-sm font-medium transition ${
                        item.adminOnly ? "mt-3 border-t border-line pt-3 " : ""
                      }${
                        isActive ? "bg-blue-50 text-blue-700" : "text-slate-600 hover:bg-slate-100"
                      }`
                    }
                  >
                    <Icon size={18} aria-hidden="true" />
                    <span>{item.label}</span>
                    {unreadCount > 0 ? (
                      <span className="ml-auto inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-rose-600 px-1 text-[11px] font-bold text-white">
                        {unreadCount > 99 ? "99+" : unreadCount}
                      </span>
                    ) : null}
                  </NavLink>
                );
              })}
            </nav>

            <div className="rounded-md border border-line p-3">
              <div className="text-sm font-semibold text-ink">{user?.displayName}</div>
              <div className="mt-1 flex flex-wrap items-center gap-2 text-xs font-medium text-blue-700">
                <span>{user ? displayRoleName(user) : "-"}</span>
                {user?.isSystemAdmin && user.businessScope === "organization" ? (
                  <span className="rounded-full bg-violet-50 px-2 py-0.5 text-violet-700">系统管理员</span>
                ) : null}
              </div>
              <p className="mt-2 text-xs leading-5 text-muted">
                {user ? (roleDescriptions[user.role] || "按角色权限参与招聘流程。") : ""}
              </p>
            </div>

            <button
              className="flex w-full items-center justify-center gap-2 rounded-md border border-line px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
              type="button"
              onClick={() => {
                logout();
                setNavOpen(false);
                navigate("/login", { replace: true });
              }}
            >
              <LogOut size={16} aria-hidden="true" />
              退出登录
            </button>
          </div>
        </FloatingShellPanel>
      ) : null}

      {statusOpen ? (
        <FloatingShellPanel side="right" title="当前流程状态" onClose={() => setStatusOpen(false)}>
          {applicationSummary ? (
            <div className="space-y-4">
              <div className="rounded-md border border-line p-4">
                <div className="text-sm font-semibold">{applicationSummary.candidateName}</div>
                <div className="mt-1 text-xs text-muted">{applicationSummary.jobTitle}</div>
                <Badge className={`mt-3 ${statusTone[applicationSummary.status]}`}>{statusLabels[applicationSummary.status]}</Badge>
              </div>

              <div>
                <div className="mb-2 text-xs font-semibold uppercase tracking-normal text-muted">招聘流程时间</div>
                <div className="space-y-2">
                  {timeline.map((item) => (
                    <div key={item.eventId} className="rounded-md border border-line p-3">
                      <div className="text-sm font-medium text-ink">{item.note}</div>
                      <div className="mt-1 text-xs text-slate-600">
                        {new Date(item.effectiveAt).toLocaleString("zh-CN")}
                      </div>
                      <div className="mt-1 text-[11px] text-muted">
                        {item.actorName} · 录入于 {new Date(item.recordedAt).toLocaleString("zh-CN")}
                      </div>
                    </div>
                  ))}
                  {timeline.length === 0 ? <p className="text-sm text-muted">尚未记录业务时间。</p> : null}
                </div>
              </div>

            </div>
          ) : (
            <p className="text-sm text-muted">当前页面没有选中的候选申请。</p>
          )}
        </FloatingShellPanel>
      ) : null}

      <main className="min-h-screen px-4 py-4 lg:px-6">
        <Outlet />
      </main>
    </div>
  );
}

function FloatingShellPanel({
  side,
  title,
  children,
  onClose
}: {
  side: "left" | "right";
  title: string;
  children: ReactNode;
  onClose: () => void;
}) {
  return (
    <aside
      className={`fixed bottom-4 top-16 z-40 w-[min(360px,calc(100vw-2rem))] overflow-hidden rounded-md border border-line bg-white/95 shadow-2xl backdrop-blur ${
        side === "left" ? "left-4" : "right-4"
      }`}
    >
      <div className="flex items-center justify-between border-b border-line px-4 py-3">
        <div className="text-sm font-semibold text-ink">{title}</div>
        <button
          className="inline-flex h-8 w-8 items-center justify-center rounded-md text-slate-600 hover:bg-slate-100"
          type="button"
          aria-label={`关闭${title}`}
          onClick={onClose}
        >
          <X size={16} aria-hidden="true" />
        </button>
      </div>
      <div className="h-[calc(100%-3.5rem)] overflow-y-auto p-3">{children}</div>
    </aside>
  );
}
