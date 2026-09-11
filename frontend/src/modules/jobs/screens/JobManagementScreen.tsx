import { Eye, FileClock, FileUp, Pencil, RefreshCw, Search, Trash2, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { Button } from "@/shared/ui/Button";
import { PageHeader } from "@/shared/ui/PageHeader";
import { Section } from "@/shared/ui/Section";
import { HardScreeningPolicyPanel } from "@/modules/assessment/components/HardScreeningPolicyPanel";
import { ImportRecordDrawer } from "@/modules/documents/components/ImportRecordDrawer";
import { JobRequirementImportPanel } from "@/modules/jobs/components/JobRequirementImportPanel";
import { JobInterviewGuidePanel } from "@/modules/jobs/components/JobInterviewGuidePanel";
import { getJobProfileWorkflowTimeline, type JobUpdateInput, type ManagedJob } from "@/modules/jobs/api";
import { useJobManagementController } from "@/modules/jobs/hooks/useJobManagementController";
import { Badge } from "@/shared/ui/Badge";
import { RowActionMenu } from "@/shared/ui/RowActionMenu";
import { hasBusinessPermission } from "@/modules/auth/permissions";
import { hasRecoveryAction, recoveryAction } from "@/shared/recovery/actions";
import { WorkflowExecutionTimeline } from "@/shared/workflows/WorkflowExecutionTimeline";
import type { WorkflowExecutionEvent } from "@/shared/workflows/process";
import { toUserFacingError } from "@/shared/utils/displayText";
import { jobConfigurationLabel } from "@/modules/jobs/jobConfiguration";

const jobStatusLabels: Record<string, string> = {
  open: "招聘中",
  setup_pending: "负责人未配置",
  closed: "已关闭",
  draft: "草稿"
};

function jobStatusLabel(job: ManagedJob): string {
  return job.status === "setup_pending"
    ? job.missingJobAssignments.length
      ? jobConfigurationLabel(job.missingJobAssignments)
      : "负责人未配置"
    : jobStatusLabels[job.status] ?? "招聘状态更新中";
}

const jobStatusTone: Record<string, string> = {
  open: "border-emerald-500 bg-emerald-100 text-emerald-800",
  setup_pending: "border-amber-400 bg-amber-50 text-amber-800",
  closed: "border-rose-500 bg-rose-100 text-rose-800",
  draft: "border-slate-300 bg-slate-100 text-slate-700"
};
const profileStatusView: Record<string, { label: string; tone: string }> = {
  not_started: { label: "未开始生成", tone: "border-slate-200 bg-slate-50 text-slate-600" },
  queued: { label: "画像任务排队中", tone: "border-blue-200 bg-blue-50 text-blue-700" },
  processing: { label: "正在生成岗位画像", tone: "border-blue-200 bg-blue-50 text-blue-700" },
  ready: { label: "画像已就绪", tone: "border-emerald-200 bg-emerald-50 text-emerald-700" },
  review_required: { label: "画像待确认", tone: "border-amber-200 bg-amber-50 text-amber-800" },
  failed: { label: "画像生成失败", tone: "border-rose-200 bg-rose-50 text-rose-700" }
};

function profileStatusDisplay(status: string) {
  return profileStatusView[status] ?? {
    label: "画像状态更新中",
    tone: "border-slate-200 bg-slate-50 text-slate-600",
  };
}

export function JobManagementScreen() {
  const [searchParams] = useSearchParams();
  const openedJobId = useRef("");
  const {
    token,
    user,
    jobs,
    filtered,
    keyword,
    setKeyword,
    status,
    setStatus,
    loading,
    error,
    selectedJob,
    setSelectedJob,
    importOpen,
    setImportOpen,
    recordsOpen,
    setRecordsOpen,
    reviewDocumentId,
    setReviewDocumentId,
    jobImportUnreadCount,
    setJobImportUnreadCount,
    deletingJobId,
    profileEditJobId,
    setProfileEditJobId,
    profileActionJobId,
    runProfileAction,
    loadJobs,
    loadJobImportUnreadCount,
    reviewImport,
    updateJob,
    deleteJob
  } = useJobManagementController();
  const canImportJobs = hasBusinessPermission(user, "job_document.upload");
  const canEditJobs = hasBusinessPermission(user, "job.edit");
  const canManageHardScreening = hasBusinessPermission(user, "hard_screening.policy.manage");
  useEffect(() => {
    const targetJobId = searchParams.get("jobId") ?? "";
    if (!targetJobId || openedJobId.current === targetJobId) return;
    const target = jobs.find((job) => job.jobId === targetJobId);
    if (!target) return;
    openedJobId.current = targetJobId;
    setSelectedJob(target);
  }, [jobs, searchParams, setSelectedJob]);
  return (
    <>
      <PageHeader
        title="岗位管理"
        description="查看正式岗位、岗位要求来源和招聘配置。"
      />
      <Section
        title="岗位列表"
        description={`共 ${jobs.length} 个岗位，按状态筛选并维护岗位配置`}
        action={canImportJobs ? (
          <div className="flex items-center gap-2">
            <div className="relative">
              <Button className="h-9 px-2.5 text-xs" type="button" variant="ghost" onClick={() => setRecordsOpen(true)}>
                <FileClock size={15} />导入记录
              </Button>
              {jobImportUnreadCount > 0 ? (
                <span className="absolute -right-1 -top-1 inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-rose-600 px-1 text-[10px] font-bold text-white">
                  {jobImportUnreadCount > 99 ? "99+" : jobImportUnreadCount}
                </span>
              ) : null}
            </div>
            <Button className="h-9 px-3 text-xs" type="button" variant="primary" onClick={() => {
              setReviewDocumentId("");
              setImportOpen(true);
            }}>
              <FileUp size={15} />导入招聘要求
            </Button>
          </div>
        ) : undefined}
      >
        <div className="mb-4 flex flex-col gap-2 lg:flex-row lg:items-center">
          <div className="flex flex-col gap-2 sm:flex-row">
            <label className="relative w-full sm:w-80 lg:w-96">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" size={17} />
              <input className="h-9 w-full rounded-md border border-slate-300 bg-white pl-9 pr-3 text-sm outline-none transition focus:border-blue-400 focus:ring-2 focus:ring-blue-100" placeholder="搜索岗位、部门或来源文件" value={keyword} onChange={(event) => setKeyword(event.target.value)} />
            </label>
            <select className="h-9 rounded-md border border-slate-300 bg-white px-2.5 text-sm" value={status} onChange={(event) => setStatus(event.target.value)}>
              <option value="all">全部状态</option>
              <option value="open">招聘中</option>
              <option value="setup_pending">负责人未配置</option>
              <option value="closed">已关闭</option>
            </select>
          </div>
        </div>
        {error ? <p className="mb-3 rounded-md bg-rose-50 p-3 text-sm text-rose-700">{error}</p> : null}
        <div className="overflow-x-auto rounded-md border border-line">
          <table className="data-table data-table-fixed w-full min-w-[1060px] border-collapse text-left text-sm">
            <colgroup>
              <col className="w-[220px]" />
              <col className="w-[170px]" />
              <col className="w-[110px]" />
              <col className="w-[110px]" />
              <col className="w-[190px]" />
              <col className="w-[220px]" />
              <col className="w-[120px]" />
            </colgroup>
            <thead className="bg-slate-50 text-xs text-muted">
              <tr>
                <th className="px-4 py-2.5 font-medium">岗位</th>
                <th className="px-4 py-2.5 font-medium">部门</th>
                <th className="data-table-number px-4 py-2.5">招聘人数</th>
                <th className="data-table-number px-4 py-2.5">候选人数</th>
                <th className="px-4 py-2.5 font-medium">招聘与画像状态</th>
                <th className="px-4 py-2.5 font-medium">来源</th>
                <th className="data-table-action px-4 py-2.5">操作</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((job) => (
                <tr key={job.jobId} className="border-t border-line hover:bg-slate-50">
                  <td className="px-4 py-3">
                    <div className="data-table-primary">{job.title}</div>
                  </td>
                  <td className="px-4 py-3">{job.departmentName || "未设置"}</td>
                  <td className="data-table-number px-4 py-3">{job.headcount ?? "未设置"}</td>
                  <td className="data-table-number px-4 py-3">{job.candidateCount}</td>
                  <td className="px-4 py-3">
                    <Badge className={jobStatusTone[job.status] ?? jobStatusTone.draft}>{jobStatusLabel(job)}</Badge>
                    <div className="mt-1"><Badge className={profileStatusDisplay(job.profileStatus).tone}>{profileStatusDisplay(job.profileStatus).label}</Badge></div>
                    {job.profileDegraded ? <div className="mt-1 text-xs text-amber-700">画像包含降级结果</div> : null}
                    {job.waitingApplicationCount > 0 ? <div className="mt-1 text-xs text-amber-700">
                      {job.profileStatus === "review_required"
                        ? `等待画像修复后初步筛选：${job.waitingApplicationCount} 人`
                        : job.profileStatus === "ready"
                          ? `等待启动初步筛选：${job.waitingApplicationCount} 人`
                          : `等待画像后初步筛选：${job.waitingApplicationCount} 人`}
                    </div> : null}
                  </td>
                  <td className="max-w-56 truncate px-4 py-3 text-xs text-muted">{job.sourceFilename ?? "来源未记录"}</td>
                  <td className="data-table-action px-4 py-3">
                    <RowActionMenu ariaLabel={`${job.title} 的操作`} items={[
                      { label: "查看岗位详情", icon: <Eye size={15} />, onSelect: () => { setProfileEditJobId(""); setSelectedJob(job); } },
                      ...(hasRecoveryAction(job.availableActions, "edit_job_profile") ? [{
                        label: recoveryAction(job.availableActions, "edit_job_profile")?.label ?? "编辑岗位源要求",
                        icon: <Pencil size={15} />,
                        onSelect: () => { setProfileEditJobId(job.jobId); setSelectedJob(job); }
                      }] : []),
                      ...((hasRecoveryAction(job.availableActions, "retry_profile") || hasRecoveryAction(job.availableActions, "retry_job_profile")) ? [{ label: profileActionJobId === job.jobId ? "提交中" : (recoveryAction(job.availableActions, "retry_job_profile") || recoveryAction(job.availableActions, "retry_profile"))?.label ?? "重试生成岗位画像", icon: <RefreshCw size={15} />, disabled: profileActionJobId === job.jobId, onSelect: () => void runProfileAction(job, "retry") }] : []),
                      ...((hasRecoveryAction(job.availableActions, "regenerate_profile") || hasRecoveryAction(job.availableActions, "regenerate_job_profile")) ? [{ label: profileActionJobId === job.jobId ? "提交中" : (recoveryAction(job.availableActions, "regenerate_job_profile") || recoveryAction(job.availableActions, "regenerate_profile"))?.label ?? "重新生成岗位画像", icon: <RefreshCw size={15} />, disabled: profileActionJobId === job.jobId, onSelect: () => void runProfileAction(job, "regenerate") }] : []),
                      ...(hasBusinessPermission(user, "job.delete") ? [{
                        label: deletingJobId === job.jobId ? "删除中" : "删除岗位",
                        icon: <Trash2 size={15} />,
                        disabled: Boolean(deletingJobId),
                        destructive: true,
                        onSelect: () => {
                          if (window.confirm(`确认删除岗位“${job.title}”吗？\n\n删除后岗位将停止接收新简历，历史候选人记录仍会保留。`)) {
                            void deleteJob(job);
                          }
                        }
                      }] : [])
                    ]} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {loading ? <p className="mt-4 text-sm text-muted">正在加载岗位……</p> : null}
        {!loading && filtered.length === 0 ? <p className="mt-4 text-sm text-muted">没有符合条件的岗位。</p> : null}
      </Section>

      {importOpen ? (
        <div className="fixed inset-0 z-50 overflow-y-auto bg-slate-950/40 p-4" role="dialog" aria-modal="true">
          <div className="mx-auto max-w-5xl rounded-lg bg-white p-5 shadow-xl">
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-lg font-semibold text-ink">{reviewDocumentId ? "确认岗位提取结果" : "导入招聘要求"}</h2>
              <button className="rounded-md p-2 text-muted hover:bg-slate-100" onClick={() => setImportOpen(false)} aria-label="关闭"><X size={18} /></button>
            </div>
            <JobRequirementImportPanel initialDocumentId={reviewDocumentId} onConfirmed={() => void loadJobs()} />
          </div>
        </div>
      ) : null}
      {selectedJob ? (
        <JobDetailDrawer
          job={selectedJob}
          token={token}
          canEdit={canEditJobs}
          canManageHardScreening={canManageHardScreening}
          onUpdate={async (input) => {
            const updated = await updateJob(selectedJob, input);
            if (updated) setProfileEditJobId("");
            return updated;
          }}
          regenerating={profileActionJobId === selectedJob.jobId}
          onRegenerate={() => void runProfileAction(selectedJob, "regenerate")}
          initialEditing={profileEditJobId === selectedJob.jobId}
          onClose={() => { setProfileEditJobId(""); setSelectedJob(undefined); }}
        />
      ) : null}
      <ImportRecordDrawer
        open={recordsOpen}
        type="job"
        onRead={() => setJobImportUnreadCount(0)}
        onClose={() => {
          setRecordsOpen(false);
          void loadJobImportUnreadCount();
        }}
        onReviewJobImport={reviewImport}
      />
    </>
  );
}

function JobDetailDrawer({
  job,
  token,
  canEdit,
  canManageHardScreening,
  regenerating,
  initialEditing,
  onUpdate,
  onRegenerate,
  onClose
}: {
  job: ManagedJob;
  token: string | null;
  canEdit: boolean;
  canManageHardScreening: boolean;
  regenerating: boolean;
  initialEditing: boolean;
  onUpdate: (input: JobUpdateInput) => Promise<ManagedJob | undefined>;
  onRegenerate: () => void;
  onClose: () => void;
}) {
  const [tab, setTab] = useState<"content" | "screening" | "interview">("content");
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [draft, setDraft] = useState<JobUpdateInput>(() => toJobUpdateInput(job));
  const [timelineOpen, setTimelineOpen] = useState(false);
  const [timelineEvents, setTimelineEvents] = useState<WorkflowExecutionEvent[]>([]);
  const [timelineLoading, setTimelineLoading] = useState(false);
  const [timelineError, setTimelineError] = useState("");

  useEffect(() => {
    setEditing(initialEditing);
    setDraft(toJobUpdateInput(job));
  }, [job, initialEditing]);

  async function save() {
    if (saving) return;
    setSaving(true);
    const updated = await onUpdate(draft);
    setSaving(false);
    if (updated) setEditing(false);
  }  const loadTimeline = async () => {
    if (!token || !job.profileWorkflowRunId) return;
    setTimelineLoading(true);
    setTimelineError("");
    try {
      const view = await getJobProfileWorkflowTimeline(token, job.jobId, job.profileWorkflowRunId);
      setTimelineEvents(view.items);
    } catch (reason) {
      setTimelineError(reason instanceof Error ? reason.message : "岗位画像执行轨迹加载失败");
    } finally {
      setTimelineLoading(false);
    }
  };

  useEffect(() => {
    if (timelineOpen) void loadTimeline();
  }, [timelineOpen, job.jobId, job.profileWorkflowRunId, token]);
  return (
    <div className="fixed inset-0 z-50 bg-slate-950/30" role="dialog" aria-modal="true">
      <aside className="absolute bottom-0 right-0 top-0 w-full max-w-3xl overflow-y-auto bg-white p-5 shadow-2xl">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 className="text-xl font-semibold text-ink">{job.title}</h2>
            <p className="mt-1 text-sm text-muted">{job.departmentName || "未设置部门"} · {jobStatusLabel(job)}</p>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <Badge className={profileStatusDisplay(job.profileStatus).tone}>{profileStatusDisplay(job.profileStatus).label}</Badge>
              {job.canRouteCandidate ? <span className="text-xs text-emerald-700">可接收简历分发</span> : <span className="text-xs text-slate-500">暂不可分发</span>}
            </div>
          </div>
          <div className="flex items-center gap-2">
            {tab === "content" && canEdit ? (
              editing ? (
                <>
                  <Button disabled={saving} onClick={() => { setDraft(toJobUpdateInput(job)); setEditing(false); }}>取消</Button>
                  <Button variant="primary" disabled={saving} onClick={() => void save()}>{saving ? "正在保存" : "保存修改"}</Button>
                </>
              ) : <Button onClick={() => setEditing(true)}><Pencil size={15} />编辑岗位</Button>
            ) : null}
            <button className="rounded-md p-2 text-muted hover:bg-slate-100" onClick={onClose} aria-label="关闭岗位详情"><X size={18} /></button>
          </div>
        </div>
        <div className="mt-5 flex gap-1 border-b border-line">
          <DetailTab active={tab === "content"} onClick={() => setTab("content")}>岗位内容</DetailTab>
          {canManageHardScreening ? <DetailTab active={tab === "screening"} onClick={() => setTab("screening")}>硬筛策略</DetailTab> : null}
          {canEdit ? <DetailTab active={tab === "interview"} onClick={() => setTab("interview")}>岗位面试题单</DetailTab> : null}
        </div>
        {tab === "content" ? (
          <JobContentEditor job={job} editing={editing} draft={draft} onChange={setDraft} canEdit={canEdit} regenerating={regenerating} onRegenerate={onRegenerate} onViewTimeline={() => setTimelineOpen(true)} />
        ) : tab === "screening" && canManageHardScreening ? (
          <div className="mt-5">
            <HardScreeningPolicyPanel jobId={job.jobId} />
          </div>
        ) : tab === "interview" && canEdit ? (
          <JobInterviewGuidePanel token={token} jobId={job.jobId} />
        ) : null}
        {timelineOpen ? <div className="absolute inset-0 z-10 flex items-center justify-center bg-slate-950/30 p-4"><div className="flex max-h-[82vh] w-full max-w-2xl flex-col overflow-hidden rounded-lg bg-white shadow-xl"><div className="flex items-start justify-between gap-3 border-b border-line p-4"><div><h3 className="font-semibold text-ink">岗位画像执行轨迹</h3><p className="mt-1 text-sm text-muted">{job.title}</p></div><button type="button" className="text-sm text-muted hover:text-ink" onClick={() => setTimelineOpen(false)}>关闭</button></div><div className="min-h-0 flex-1 overflow-auto p-4"><WorkflowExecutionTimeline events={timelineEvents} loading={timelineLoading} error={timelineError} /></div></div></div> : null}
      </aside>
    </div>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return <div className="rounded-md border border-line bg-slate-50 p-3"><dt className="text-xs text-muted">{label}</dt><dd className="mt-1 text-sm font-medium text-ink">{value}</dd></div>;
}

function DetailTab({ active, onClick, children }: { active: boolean; onClick: () => void; children: string }) {
  return (
    <button type="button" className={`border-b-2 px-4 py-2.5 text-sm font-medium ${active ? "border-blue-600 text-blue-700" : "border-transparent text-muted hover:text-ink"}`} onClick={onClick}>
      {children}
    </button>
  );
}

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString("zh-CN");
}

function toJobUpdateInput(job: ManagedJob): JobUpdateInput {
  return {
    title: job.title,
    headcount: job.headcount,
    jdText: job.jdText,
    responsibilities: [...job.responsibilities],
    qualifications: [...job.qualifications],
    educationRequirement: job.educationRequirement,
    majorRequirement: job.majorRequirement
  };
}

function JobContentEditor({
  job,
  editing,
  draft,
  onChange,
  canEdit,
  regenerating,
  onRegenerate,
  onViewTimeline
}: {
  job: ManagedJob;
  editing: boolean;
  draft: JobUpdateInput;
  onChange: (value: JobUpdateInput) => void;
  canEdit: boolean;
  regenerating: boolean;
  onRegenerate: () => void;
  onViewTimeline: () => void;
}) {
  return <>
    <section className="mt-5 rounded-md border border-line bg-slate-50 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-ink">岗位能力画像</h3>
          <p className="mt-1 text-sm text-slate-700">{profileStatusDisplay(job.profileStatus).label}</p>
          <p className="mt-1 text-xs leading-5 text-muted">{job.canStartScreening ? "当前已有可用画像，新申请可进入初步筛选。" : "候选人可以先分发到该岗位；画像完成后会自动进入初步筛选。"}</p>
          {job.profileWorkflowRunId ? <button type="button" className="mt-2 text-xs font-medium text-blue-700 hover:text-blue-900" onClick={onViewTimeline}>查看画像执行轨迹</button> : null}
          {job.profileErrorMessage ? <p className="mt-2 max-w-xl text-xs leading-5 text-rose-700">{toUserFacingError(job.profileErrorMessage)}</p> : null}
          {job.profileDegraded ? (
            <div className="mt-3 flex flex-wrap items-center gap-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2">
              <p className="min-w-0 flex-1 text-xs leading-5 text-amber-900">{job.profileQualityMessage ?? "岗位画像包含降级生成的能力，请检查后决定是否重新生成。"}</p>
              {hasRecoveryAction(job.availableActions, "regenerate_job_profile") ? <Button className="h-8 px-2.5 text-xs" disabled={regenerating} onClick={onRegenerate}><RefreshCw size={14} />{regenerating ? "提交中" : "重新生成画像"}</Button> : null}
            </div>
          ) : null}
          {job.waitingApplicationCount > 0 ? <p className="mt-2 text-xs text-amber-800">有 {job.waitingApplicationCount} 个申请正在等待本岗位画像完成。</p> : null}
        </div>
      </div>
    </section>
    <dl className="mt-5 grid gap-3 sm:grid-cols-3">
      <Detail label="招聘人数" value={String(job.headcount ?? "未设置")} />
      <Detail label="候选人数" value={String(job.candidateCount)} />
      <Detail label="部门主管" value={job.hiringManagerName ?? "未设置"} />
      <Detail label="招聘人" value={job.departmentRecruiterName ?? "未设置"} />
      <Detail label="学历要求" value={job.educationRequirement ?? "见岗位正文"} />
      <Detail label="专业要求" value={job.majorRequirement ?? "未设置"} />
      <Detail label="来源" value={job.sourceFilename ?? "来源未记录"} />
      <Detail label="开放时间" value={formatDate(job.openedAt)} />
    </dl>
    {editing ? (
      <div className="mt-5 space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="block text-sm font-medium text-slate-700">岗位名称<input className="mt-1.5 h-10 w-full rounded-md border border-slate-300 bg-white px-3 text-sm" value={draft.title} onChange={(event) => onChange({ ...draft, title: event.target.value })} /></label>
          <label className="block text-sm font-medium text-slate-700">招聘人数<input className="mt-1.5 h-10 w-full rounded-md border border-slate-300 bg-white px-3 text-sm" type="number" min="1" value={draft.headcount ?? ""} onChange={(event) => onChange({ ...draft, headcount: event.target.value ? Number(event.target.value) : undefined })} /></label>
          <label className="block text-sm font-medium text-slate-700">学历要求<input className="mt-1.5 h-10 w-full rounded-md border border-slate-300 bg-white px-3 text-sm" value={draft.educationRequirement ?? ""} onChange={(event) => onChange({ ...draft, educationRequirement: event.target.value })} /></label>
          <label className="block text-sm font-medium text-slate-700">专业要求<input className="mt-1.5 h-10 w-full rounded-md border border-slate-300 bg-white px-3 text-sm" value={draft.majorRequirement ?? ""} onChange={(event) => onChange({ ...draft, majorRequirement: event.target.value })} /></label>
        </div>
        <label className="block text-sm font-medium text-slate-700">完整岗位要求<textarea className="mt-1.5 min-h-[96px] w-full resize-y [field-sizing:content] rounded-md border border-slate-300 bg-white px-4 py-3 text-sm leading-7 text-slate-700 outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100" value={draft.jdText} onChange={(event) => onChange({ ...draft, jdText: event.target.value })} /></label>
      </div>
    ) : <>
      <section className="mt-5"><h3 className="text-sm font-semibold text-ink">完整岗位要求</h3>{job.jdText.trim() ? <div className="mt-2 whitespace-pre-wrap rounded-md border border-line bg-slate-50 px-4 py-3 text-sm leading-7 text-slate-700">{job.jdText}</div> : <p className="mt-2 text-sm text-muted">岗位正文尚未录入。</p>}</section>
    </>}
  </>;
}
