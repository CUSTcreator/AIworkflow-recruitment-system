import { CheckCircle2, ListChecks, Search } from "lucide-react";
import { useNavigate } from "react-router-dom";
import type { TaskListItem } from "@/modules/tasks/api";
import { displayRoleName } from "@/modules/auth/accessPolicy";
import { statusLabels, statusTone } from "@/modules/applications/status";
import { Badge } from "@/shared/ui/Badge";
import { Button } from "@/shared/ui/Button";
import { PageHeader } from "@/shared/ui/PageHeader";
import { StatCard } from "@/shared/ui/StatCard";
import { Section } from "@/shared/ui/Section";
import { SortSelect } from "@/shared/ui/SortSelect";
import { useTaskCenterController } from "@/modules/tasks/hooks/useTaskCenterController";

export function TaskCenterScreen() {
  const {
    user,
    view,
    loading,
    error,
    keyword,
    setKeyword,
    jobs,
    jobId,
    setJobId,
    sort,
    setSort,
    page,
    setPage,
    pageSize,
    setPageSize
  } = useTaskCenterController();
  const actionableItems = view.items;
  return (
    <>
      <PageHeader
        eyebrow="任务中心"
        title={`${user ? displayRoleName(user) : "-"}的待办任务`}
        description="集中查看当前需要处理的招聘事项，并进入对应工作台。"
      />

      <div className="mb-5 max-w-xs">
        <StatCard label="匹配任务" value={view.total} tone="blue" icon={<ListChecks size={18} />} />
      </div>

      <Section title="我的待办" description="需要你处理的招聘事项。">
        <div className="mb-4 flex flex-col gap-2 lg:flex-row lg:items-center">
          <label className="relative w-full lg:max-w-md lg:flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" size={17} />
            <input
              className="h-9 w-full rounded-md border border-slate-300 bg-white pl-9 pr-3 text-sm outline-none transition focus:border-blue-400 focus:ring-2 focus:ring-blue-100"
              placeholder="搜索候选人、岗位或任务"
              value={keyword}
              onChange={(event) => setKeyword(event.target.value)}
            />
          </label>
          <select
            className="h-9 rounded-md border border-slate-300 bg-white px-2.5 text-sm lg:w-56"
            value={jobId}
            onChange={(event) => {
              setJobId(event.target.value);
              setPage(1);
            }}
          >
            <option value="">全部岗位</option>
            {jobs.map((job) => <option key={job.jobId} value={job.jobId}>{job.title}</option>)}
          </select>
          <SortSelect
            className="lg:w-56"
            value={sort}
            onChange={(event) => {
              setSort(event.target.value as typeof sort);
              setPage(1);
            }}
          >
            <option value="priority">待办优先级</option>
            <option value="score_desc">能力分从高到低</option>
            <option value="score_asc">能力分从低到高</option>
          </SortSelect>
        </div>

        {error ? <div className="mb-4 rounded-md border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{error}</div> : null}

        {loading ? (
          <div className="rounded-md border border-dashed border-line p-8 text-center text-sm text-muted">正在加载待办任务…</div>
        ) : actionableItems.length === 0 ? (
          <div className="rounded-md border border-dashed border-line p-8 text-center">
            <CheckCircle2 className="mx-auto text-emerald-600" size={32} />
            <p className="mt-3 text-sm text-muted">当前角色暂无待办任务。</p>
          </div>
        ) : (
          <TaskTable items={actionableItems} />
        )}

        <div className="mt-3 flex flex-wrap items-center justify-between gap-3 text-sm text-muted">
          <span>共 {view.total} 项，第 {page} / {Math.max(1, Math.ceil(view.total / pageSize))} 页</span>
          <div className="flex items-center gap-2">
            <select
              className="h-9 rounded-md border border-line bg-white px-2"
              value={pageSize}
              onChange={(event) => {
                setPageSize(Number(event.target.value));
                setPage(1);
              }}
            >
              <option value={20}>每页 20 项</option>
              <option value={30}>每页 30 项</option>
              <option value={50}>每页 50 项</option>
            </select>
            <Button className="h-8 px-2.5 text-xs" disabled={page <= 1} onClick={() => setPage((current) => current - 1)}>上一页</Button>
            <Button
              className="h-8 px-2.5 text-xs"
              disabled={page >= Math.max(1, Math.ceil(view.total / pageSize))}
              onClick={() => setPage((current) => current + 1)}
            >
              下一页
            </Button>
          </div>
        </div>
      </Section>

    </>
  );
}

function TaskTable({ items }: { items: TaskListItem[] }) {
  const navigate = useNavigate();
  return (
    <div className="overflow-x-auto rounded-md border border-line">
      <table className="data-table data-table-fixed w-full min-w-[1040px] border-collapse text-left text-sm">
        <colgroup>
          <col className="w-[190px]" />
          <col className="w-[230px]" />
          <col className="w-[190px]" />
          <col className="w-[160px]" />
          <col className="w-[120px]" />
          <col className="w-[150px]" />
        </colgroup>
        <thead className="bg-slate-50 text-xs font-medium text-slate-500">
          <tr>
            <th className="px-4 py-2.5 font-medium">候选人</th>
            <th className="px-4 py-2.5 font-medium">待办事项</th>
            <th className="px-4 py-2.5 font-medium">岗位</th>
            <th className="px-4 py-2.5 font-medium">当前阶段</th>
            <th className="data-table-number px-4 py-2.5">当前能力分</th>
            <th className="data-table-action px-4 py-2.5">操作</th>
          </tr>
        </thead>
        <tbody>
          {items.map((task) => (
            <tr key={task.taskId} className="border-t border-line bg-white transition hover:bg-slate-50/70">
              <td className="px-4 py-3">
                <div className="data-table-primary">{task.candidateName}</div>
                <div className="data-table-secondary">
                  {[task.highestDegree, task.school].filter(Boolean).join(" · ") || "教育信息未结构化"}
                </div>
              </td>
              <td className="px-4 py-3 font-medium text-ink">
                <span className="flex min-w-0 items-start gap-1.5">
                  <span className="truncate">{task.title}</span>
                </span>
              </td>
              <td className="px-4 py-3">
                <div className="truncate" title={task.jobTitle}>{task.jobTitle}</div>
                {task.jobMajorRequirement ? <div className="mt-1 truncate text-xs text-muted" title={task.jobMajorRequirement}>专业要求：{task.jobMajorRequirement}</div> : null}
              </td>
              <td className="px-4 py-3">
                <Badge className={statusTone[task.applicationStatus]}>{statusLabels[task.applicationStatus]}</Badge>
              </td>
              <td className="data-table-number px-4 py-3 font-semibold text-ink">
                {task.currentScore == null ? "待评分" : task.currentScore.toFixed(1)}
              </td>
              <td className="data-table-action px-4 py-3">
                <Button
                  className="h-8 px-2.5 text-xs"
                  type="button"
                  variant="primary"
                  onClick={() => navigate(task.workbenchAvailable === false ? "/candidates" : task.mainRoute)}
                >
                  {task.workbenchAvailable === false ? "查看招聘流程" : "查看并处理"}
                </Button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
