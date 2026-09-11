import {
  CalendarPlus,
  ChevronDown,
  Eye,
  FileText,
  PlayCircle,
  RefreshCw,
  XCircle,
} from "lucide-react";
import { useNavigate } from "react-router-dom";
import { useState, type ReactNode } from "react";
import type {
  EvidenceIndexItem,
  ResumeCapabilityFrameworkView,
} from "@/modules/assessment/contracts";
import { Button } from "@/shared/ui/Button";
import { PageHeader } from "@/shared/ui/PageHeader";
import { CandidateDecisionOverviewCard } from "@/modules/assessment/components/CandidateDecisionOverviewCard";
import { HardScreeningResultCard } from "@/modules/assessment/components/HardScreeningResultCard";
import { RecruitmentMilestoneCard } from "@/modules/applications/components/RecruitmentMilestoneCard";
import { JobCapabilityMatchPanel } from "@/modules/assessment/components/JobCapabilityMatchPanel";
import { DocumentWorkspace } from "@/modules/documents/components/DocumentWorkspace";
import { ResumeSourcePreviewDialog } from "@/modules/documents/components/ResumeSourcePreviewDialog";
import { HardScreeningPolicyPanel } from "@/modules/assessment/components/HardScreeningPolicyPanel";
import { PageLoading } from "@/shared/ui/PageLoading";
import { useScreeningReviewController } from "@/modules/assessment/hooks/useScreeningReviewController";
import { isWorkflowProcessActive } from "@/shared/workflows/process";

export function ScreeningReviewScreen() {
  const navigate = useNavigate();
  const {
    applicationId,
    context,
    loading,
    loadError,
    reload,
    workflowStatus,
    workflowPolling,
    workflowError,
    retryScoring,
    retryHardScreening,
    repairJobProfile,
    activeEvidence,
    showEvidence,
    approve,
    reject,
    canApprove,
    canReject,
    recoveryActions,
  } = useScreeningReviewController();
  const [resumeSourceOpen, setResumeSourceOpen] = useState(false);
  const [jobProfileOpen, setJobProfileOpen] = useState(false);

  const workflowRunning =
    workflowPolling ||
    isWorkflowProcessActive(workflowStatus?.process) ||
    workflowStatus?.runStatus === "queued" ||
    workflowStatus?.runStatus === "running";
  const workflowFailed =
    ["failed", "blocked"].includes(
      workflowStatus?.process?.processStatus ?? "",
    ) || ["failed", "blocked"].includes(workflowStatus?.runStatus ?? "");

  if (loading)
    return (
      <PageLoading
        label={
          workflowRunning ? "初步筛选处理中，正在等待结果" : "正在加载初步筛选结果"
        }
      />
    );
  if (loadError && workflowRunning) {
    return (
      <PageHeader
        title="初步筛选处理中"
        description="系统正在计算岗位匹配与能力评分，完成后会自动打开初步筛选结果。"
        actions={<Button onClick={() => void reload()}>立即刷新</Button>}
      />
    );
  }
  if (workflowFailed) {
    const recoveryJobId = context?.job?.jobId ?? workflowStatus?.jobId;
    const recoveryButton = (action: (typeof recoveryActions)[number]) => {
      if (action.action === "run_scoring") {
        return <Button key={action.action} onClick={() => void retryScoring()}><RefreshCw size={16} />{action.label}</Button>;
      }
      if (action.action === "repair_resume_source") {
        const submissionId = context?.application.resumeSubmissionId ?? workflowStatus?.resumeSubmissionId;
        if (!submissionId) return null;
        return <Button key={action.action} onClick={() => setResumeSourceOpen(true)}><FileText size={16} />查看简历来源</Button>;
      }
      if (action.action === "repair_job_profile") {
        return <Button key={action.action} onClick={() => setJobProfileOpen(true)}><FileText size={16} />查看并处理岗位来源</Button>;
      }
      if (action.action === "repair_hard_screening_policy") {
        return <Button key={action.action} onClick={() => setJobProfileOpen(true)}><FileText size={16} />处理硬筛条件</Button>;
      }
      return null;
    };
    return (
      <>
        <PageHeader
          title="初步筛选任务失败"
          description={
            workflowStatus?.process?.publicMessage ||
            "初步筛选处理未能完成，请在任务中心查看进度后重新提交。"
          }
          actions={<>{recoveryActions.map(recoveryButton)}</>}
        />
        {resumeSourceOpen && (context?.application.resumeSubmissionId ?? workflowStatus?.resumeSubmissionId) ? (
          <ResumeSourcePreviewDialog
            submissionId={context?.application.resumeSubmissionId ?? workflowStatus!.resumeSubmissionId!}
            candidateName={context?.candidate.displayName ?? "候选人"}
            onClose={() => setResumeSourceOpen(false)}
            onEdit={() => {
              const submissionId = context?.application.resumeSubmissionId ?? workflowStatus!.resumeSubmissionId!;
              setResumeSourceOpen(false);
              navigate(`/candidates?view=intakes&submissionId=${encodeURIComponent(submissionId)}`);
            }}
          />
        ) : null}
        {jobProfileOpen ? (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-4" role="dialog" aria-modal="true" aria-labelledby="screening-job-source-title">
            <div className="max-h-[88vh] w-full max-w-3xl overflow-auto rounded-lg bg-white p-5 shadow-xl">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h2 id="screening-job-source-title" className="font-semibold text-ink">查看并处理岗位来源</h2>
                  <p className="mt-1 text-sm text-muted">请选择重新处理本次岗位来源，或采用岗位当前要求。</p>
                </div>
                <button type="button" className="text-sm text-muted hover:text-ink" onClick={() => setJobProfileOpen(false)}>关闭</button>
              </div>
              <div className="mt-4 rounded-md bg-slate-50 p-3 text-sm leading-6 text-slate-700">
                当前申请冻结的岗位画像不可用。重新处理会保留本次申请绑定的岗位版本；采用当前岗位要求会切换到岗位最新版本。
              </div>
              {recoveryJobId && recoveryActions.some((item) => item.action === "repair_hard_screening_policy") ? (
                <div className="mt-4"><HardScreeningPolicyPanel jobId={recoveryJobId} onSaved={(policy) => { setJobProfileOpen(false); if (policy.enabled) void retryHardScreening(); }} /></div>
              ) : null}
              {!recoveryActions.some((item) => item.action === "repair_hard_screening_policy") ? <div className="mt-5 flex flex-wrap justify-end gap-2">
                <Button onClick={() => setJobProfileOpen(false)}>取消</Button>
                <Button onClick={() => { setJobProfileOpen(false); void repairJobProfile("reprocess_frozen"); }}><RefreshCw size={16} />重新处理本次岗位来源</Button>
                <Button variant="primary" onClick={() => { setJobProfileOpen(false); void repairJobProfile("adopt_current"); }}><RefreshCw size={16} />采用当前岗位要求</Button>
              </div> : <div className="mt-5 flex justify-end"><Button onClick={() => setJobProfileOpen(false)}>关闭</Button></div>}
            </div>
          </div>
        ) : null}
      </>
    );
  }
  if (loadError) {
    return (
      <PageHeader
        title="初步筛选审核加载失败"
        description={loadError}
        actions={<Button onClick={() => void reload()}>重新加载</Button>}
      />
    );
  }
  if (!context) return <PageHeader title="未找到候选申请" />;

  const { application, candidate, job, hardScreening, screeningResult } = context;
  function evidenceButton(ids: string[]) {
    const evidenceId =
      ids.find((id) => screeningResult.evidenceIndex[id]) ?? ids[0];
    if (!evidenceId) return null;
    return (
      <button
        type="button"
        className="inline-flex items-center gap-1 text-xs font-semibold text-blue-700 hover:text-blue-900"
        onClick={() => void showEvidence(evidenceId)}
      >
        <Eye size={14} /> 查看依据
      </button>
    );
  }

  return (
    <div>
      <PageHeader
        title={`${candidate.displayName} · ${job.title}`}
        actions={
          application.status === "submitted" ||
          application.status === "screening_running" ? (
            <Button disabled>
              <PlayCircle size={16} /> 初步筛选处理中
            </Button>
          ) : (
            <>
              {canApprove ? (
                <Button onClick={() => void approve()} variant="primary">
                  <CalendarPlus size={16} /> 推进
                </Button>
              ) : null}
              {canReject ? (
                <Button onClick={() => void reject()} variant="danger">
                  <XCircle size={16} /> 不推进
                </Button>
              ) : null}
            </>
          )
        }
      />
      <DocumentWorkspace
        applicationId={applicationId}
        rightTitle="初步筛选审核"
        activeEvidence={activeEvidence}
        right={
          <div className="space-y-3">
            <RecruitmentMilestoneCard applicationId={applicationId} />
            <HardScreeningResultCard job={job} value={hardScreening} />
            <CandidateDecisionOverviewCard
              overview={context.decisionOverview}
              stage="screening"
              onEvidenceClick={(evidenceId) => void showEvidence(evidenceId)}
            />

            <Accordion title="岗位要求能力匹配情况" defaultOpen>
              <JobCapabilityMatchPanel
                requirements={screeningResult.jobRequirements}
                evidenceButton={evidenceButton}
              />
            </Accordion>

            {screeningResult.resumeCapabilities.length > 0 ? (
              <Accordion title="预设经历能力" defaultOpen>
                <CapabilityFrameworkGroup
                  items={screeningResult.resumeCapabilities}
                  evidenceButton={evidenceButton}
                />
              </Accordion>
            ) : null}
          </div>
        }
      />
    </div>
  );
}

function scoreText(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toFixed(1)
    : "待核验";
}

function Accordion({
  title,
  children,
  defaultOpen = false,
}: {
  title: string;
  children: ReactNode;
  defaultOpen?: boolean;
}) {
  return (
    <details
      className="group rounded-md border border-line bg-white"
      open={defaultOpen}
    >
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3 text-sm font-semibold text-ink">
        {title}
        <ChevronDown
          size={16}
          className="text-muted transition group-open:rotate-180"
        />
      </summary>
      <div className="border-t border-line p-4">{children}</div>
    </details>
  );
}

function CapabilityFrameworkGroup({
  items,
  evidenceButton,
}: {
  items: ResumeCapabilityFrameworkView[];
  evidenceButton: (ids: string[]) => ReactNode;
}) {
  const [expanded, setExpanded] = useState(false);
  const hasHiddenMiddle = items.length > 5;
  const visibleItems =
    expanded || !hasHiddenMiddle
      ? items
      : [...items.slice(0, 3), ...items.slice(-2)];
  return (
    <div className="space-y-3">
      {visibleItems.map((framework) => (
        <CapabilityFrameworkCard
          key={framework.frameworkId}
          framework={framework}
          evidenceButton={evidenceButton}
        />
      ))}
      {hasHiddenMiddle ? (
        <MiddleToggle
          expanded={expanded}
          hiddenCount={items.length - 5}
          onClick={() => setExpanded((value) => !value)}
        />
      ) : null}
    </div>
  );
}

function CapabilityFrameworkCard({
  framework,
  evidenceButton,
}: {
  framework: ResumeCapabilityFrameworkView;
  evidenceButton: (ids: string[]) => ReactNode;
}) {
  return (
    <details className="group rounded-md border border-line p-3">
      <summary className="cursor-pointer list-none">
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0 flex-1">
            <div className="flex items-center justify-between gap-3 text-sm">
              <span className="truncate font-semibold text-ink">
                {framework.frameworkName}
              </span>
              <span className="font-semibold text-ink">
                {scoreText(framework.score)}
              </span>
            </div>
            <div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-100">
              <div
                className="h-full bg-blue-600"
                style={{
                  width: `${Math.max(0, Math.min(100, framework.score ?? 0))}%`,
                }}
              />
            </div>
            <p className="mt-2 text-xs leading-5 text-muted">
              已从 {framework.activatedIndicatorCount}/
              {framework.indicatorCount} 个方面得到简历内容支持
            </p>
          </div>
          <ChevronDown
            size={16}
            className="shrink-0 text-muted transition group-open:rotate-180"
          />
        </div>
      </summary>
      <div className="mt-3 space-y-2 border-t border-line pt-3">
        {framework.indicators.map((indicator) => (
          <div
            key={indicator.indicatorId}
            className="rounded-md bg-slate-50 p-3"
          >
            <div className="flex items-center justify-between gap-3 text-sm">
              <span className="font-medium text-ink">{indicator.name}</span>
              <span className="text-xs font-semibold text-slate-700">
                {scoreText(indicator.score)}
              </span>
            </div>
            <div className="mt-2 flex items-center justify-between gap-3">
              <div className="min-w-0">
                <p className="text-[11px] leading-5 text-muted">
                  {indicator.levelDescription}
                </p>
                {indicator.primaryProjectName ? (
                  <p className="mt-1 text-[11px] text-blue-700">
                    主要证明项目：{indicator.primaryProjectName}
                  </p>
                ) : null}
              </div>
              {evidenceButton(indicator.evidenceIds)}
            </div>
          </div>
        ))}
      </div>
    </details>
  );
}

function MiddleToggle({
  expanded,
  hiddenCount,
  onClick,
}: {
  expanded: boolean;
  hiddenCount: number;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex w-full items-center justify-center gap-1 py-2 text-xs font-semibold text-blue-700 hover:text-blue-900"
    >
      {expanded ? "收起中间项" : `展开其余 ${hiddenCount} 项`}
      <ChevronDown
        size={14}
        className={`transition ${expanded ? "rotate-180" : ""}`}
      />
    </button>
  );
}
