import {
  Ellipsis,
  Eye,
  FileText,
  FolderOpen,
  HelpCircle,
  LoaderCircle,
  RefreshCw,
  Search,
  Settings,
  SlidersHorizontal,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import type { ApplicationStatus } from "@/modules/applications/contracts";
import { statusLabels, statusTone } from "../status";
import {
  getApplicationWorkflowTimeline,
  type ApplicationListItem,
  type AssessmentStageSummary,
  type HardScreeningResult,
} from "@/modules/applications/api";
import { getScreeningReviewView } from "@/modules/assessment/api";
import { readScoreSnapshot, type ScoreSnapshotView } from "@/modules/assessment/scoreSnapshotMapper";
import type { ScreeningReviewReadModel, AssessmentChangeSet } from "@/modules/assessment/contracts";
import {
  getFinalReviewView,
  getSecondInterviewReviewView,
  type FinalReviewReadModel,
  type SecondInterviewReviewReadModel,
} from "@/modules/interviews/secondInterviewApi";
import { hasRecoveryAction, recoveryAction, type RecoveryAction } from "@/shared/recovery/actions";
import { Badge } from "@/shared/ui/Badge";
import { Button } from "@/shared/ui/Button";
import { ResumeUploadDialog } from "@/modules/documents/components/ResumeUploadDialog";
import { CandidateIntakePanel } from "@/modules/documents/components/CandidateIntakePanel";
import { ResumeSourcePreviewDialog } from "@/modules/documents/components/ResumeSourcePreviewDialog";
import { getCandidateIntakeSummary } from "@/modules/documents/resumeApi";
import { HardScreeningPolicyPanel } from "@/modules/assessment/components/HardScreeningPolicyPanel";
import { PageHeader } from "@/shared/ui/PageHeader";
import { Section } from "@/shared/ui/Section";
import { SortSelect } from "@/shared/ui/SortSelect";
import { hasBusinessPermission } from "@/modules/auth/permissions";
import { DocumentStatusBadge } from "@/modules/documents/components/DocumentStatusBadge";
import { WorkflowProcessStatus } from "@/shared/ui/WorkflowProcessStatus";
import {
  isWorkflowProcessActive,
  type WorkflowExecutionEvent,
  type WorkflowProcess,
} from "@/shared/workflows/process";
import { WorkflowExecutionTimeline } from "@/shared/workflows/WorkflowExecutionTimeline";
import { toUserFacingError } from "@/shared/utils/displayText";
import { workspaceEntryState } from "@/shared/navigation/workspaceReturn";
import { ApplicationDocumentLibrary } from "@/modules/documents/components/ApplicationDocumentLibrary";
import { AuthenticatedPdfLink } from "@/modules/documents/components/AuthenticatedPdfLink";
import {
  useCandidateListController,
  type ApplicationListSort,
  type CandidateStageFilter,
  type HardScreeningListFilter,
  type SubmittedDateRange,
} from "@/modules/applications/hooks/useCandidateListController";
import { jobConfigurationLabel } from "@/modules/jobs/jobConfiguration";
import { useWorkflowActions } from "@/modules/recruitment_workflow/workflowActions";

const rejectionStageLabels: Record<
  NonNullable<ApplicationListItem["rejectionStage"]>,
  string
> = {
  hard_screening: "硬性筛选",
  screening: "初步筛选/部门审核",
  first_interview: "一面",
  hr_review: "一面后 HR 审核",
  second_interview: "二面",
  final_review: "最终决策",
};

const candidateStageOptions: Array<{
  value: CandidateStageFilter;
  label: string;
  countTone: string;
}> = [
  {
    value: "in_progress",
    label: "招聘流程中",
    countTone: "border-blue-400 text-blue-700",
  },
  {
    value: "passed",
    label: "已通过",
    countTone: "border-emerald-400 text-emerald-700",
  },
  {
    value: "rejected",
    label: "不通过",
    countTone: "border-rose-400 text-rose-700",
  },
  {
    value: "cancelled",
    label: "招聘取消",
    countTone: "border-slate-400 text-slate-700",
  },
];

const applicationStageGroups: Array<{
  label: string;
  options: Array<{ value: ApplicationStatus; label: string }>;
}> = [
  {
    label: "简历与初筛",
    options: [
      { value: "resume_processing", label: "简历解析中" },
      { value: "resume_processing_failed", label: "简历解析失败" },
      { value: "resume_review_required", label: "简历信息待确认" },
      { value: "waiting_job_profile", label: "等待岗位画像" },
      { value: "hard_screening_pending", label: "硬筛等待中" },
      { value: "hard_screening_running", label: "硬筛运行中" },
      { value: "hard_screening_review", label: "硬筛人工复核" },
      { value: "submitted", label: "已提交" },
      { value: "screening_running", label: "初步筛选运行中" },
      { value: "screening_failed", label: "初步筛选异常" },
      { value: "department_review", label: "部门初步筛选审核" },
    ],
  },
  {
    label: "面试与决策",
    options: [
      { value: "first_interview_planning", label: "一面题单确认" },
      { value: "first_interview_scheduled", label: "一面已排期" },
      { value: "first_interview_in_progress", label: "一面进行中" },
      { value: "first_interview_evaluation", label: "一面面评确认" },
      { value: "hr_second_review", label: "HR 二面审核" },
      { value: "second_interview_in_progress", label: "HR 二面进行中" },
      { value: "second_interview_evaluation", label: "HR 二面评价确认" },
      { value: "final_review", label: "最终决策" },
      { value: "on_hold", label: "暂缓" },
      { value: "manual_review", label: "人工复核" },
    ],
  },
];

type AdvancedFilterDraft = {
  hardScreeningFilter: HardScreeningListFilter;
  dateRange: SubmittedDateRange;
  customDateFrom: string;
  customDateTo: string;
  highestDegree: string;
  majorKeyword: string;
  minimumScore: string;
  overdueOnly: boolean;
  attentionOnly: boolean;
  sort: ApplicationListSort;
};

const hardScreeningFilterLabels: Record<HardScreeningListFilter, string> = {
  all: "全部硬筛结果",
  not_configured: "硬筛未启用",
  pending: "硬筛等待中",
  running: "硬筛运行中",
  processing: "硬筛处理中",
  passed: "硬筛已通过",
  failed: "硬筛未通过",
  manual_review: "硬筛待复核",
};

const dateRangeLabels: Record<SubmittedDateRange, string> = {
  all: "全部时间",
  today: "今天",
  "3d": "最近3天",
  "7d": "最近7天",
  "30d": "最近30天",
  this_month: "本月",
  "90d": "最近90天",
  custom: "自定义范围",
};

const sortLabels: Record<ApplicationListSort, string> = {
  submitted_desc: "最新投递",
  submitted_asc: "最早投递",
  score_desc: "评分从高到低",
  score_asc: "评分从低到高",
};

const hardScreeningLabels: Record<HardScreeningResult["status"], string> = {
  not_configured: "未启用",
  pending: "等待筛选",
  running: "筛选中",
  passed: "通过",
  failed: "未通过",
  manual_review: "待人工复核",
};

const hardScreeningTones: Record<HardScreeningResult["status"], string> = {
  not_configured: "border-slate-200 bg-slate-50 text-slate-600",
  pending: "border-blue-200 bg-blue-50 text-blue-700",
  running: "border-blue-200 bg-blue-50 text-blue-700",
  passed: "border-emerald-200 bg-emerald-50 text-emerald-700",
  failed: "border-rose-200 bg-rose-50 text-rose-700",
  manual_review: "border-amber-200 bg-amber-50 text-amber-800",
};

const preScreeningTones: Record<
  NonNullable<ApplicationListItem["preScreeningProcess"]>["tone"],
  string
> = {
  neutral: "border-slate-200 bg-slate-50 text-slate-700",
  info: "border-blue-200 bg-blue-50 text-blue-700",
  warning: "border-amber-200 bg-amber-50 text-amber-800",
  danger: "border-rose-200 bg-rose-50 text-rose-700",
};

type CandidateView = "applications" | "intakes";

type ApplicationTimelineTarget = {
  applicationId: string;
  candidateName: string;
  workflowRunId: string;
  label: string;
  process?: WorkflowProcess;
};

type TimelineEntry = {
  label: string;
  process: WorkflowProcess;
};

type AssessmentResultState = {
  item: ApplicationListItem;
  stage: AssessmentStageSummary["stage"];
  loading: boolean;
  error?: string;
  v1?: ScreeningReviewReadModel;
  v2?: SecondInterviewReviewReadModel;
  v3?: FinalReviewReadModel;
};

function timelineEntries(item: ApplicationListItem): TimelineEntry[] {
  // 轨迹只由后端选出的当前未完成任务提供；已完成 Workflow 只能通过阶段结果入口查看。
  const execution = item.currentExecution;
  return execution?.process.workflowRunId
    ? [{ label: `查看${execution.label}执行轨迹`, process: execution.process }]
    : [];
}

export function CandidateListScreen() {
  const location = useLocation();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const {
    retryPostFirstScoring,
    retryPostSecondScoring,
    rebuildScreeningAssessment,
    repairPostFirstScoring,
    repairPostSecondScoring,
  } = useWorkflowActions();
  const view: CandidateView =
    searchParams.get("view") === "intakes" ? "intakes" : "applications";
  const {
    token,
    user,
    applications,
    total,
    stageCounts,
    loading,
    listError,
    keyword,
    setKeyword,
    status,
    setStatus,
    departments,
    jobs,
    departmentId,
    setDepartmentId,
    jobId,
    setJobId,
    applicationStatus,
    setApplicationStatus,
    hardScreeningFilter,
    setHardScreeningFilter,
    highestDegree,
    setHighestDegree,
    majorKeyword,
    setMajorKeyword,
    minimumScore,
    setMinimumScore,
    overdueOnly,
    setOverdueOnly,
    attentionOnly,
    setAttentionOnly,
    dateRange,
    setDateRange,
    customDateFrom,
    setCustomDateFrom,
    customDateTo,
    setCustomDateTo,
    sort,
    setSort,
    page,
    setPage,
    pageSize,
    setPageSize,
    scoringIds,
    hardScreeningRetryIds,
    initialAssessmentRetryIds,
    jobProfileRepairIds,
    hardResult,
    setHardResult,
    reviewDialog,
    setReviewDialog,
    reviewReason,
    setReviewReason,
    uploadOpen,
    setUploadOpen,
    actionMenu,
    setActionMenu,
    actionMenuRef,
    actionMenuItemRef,
    deleteTarget,
    setDeleteTarget,
    documentTarget,
    setDocumentTarget,
    deleting,
    bulkDeleting,
    loadApplications,
    handleRunScoring,
    handleRepairJobProfile,
    handleRetryInitialAssessment,
    handleRetryHardScreening,
    handleHardScreeningReview,
    submitHardScreeningReview,
    showHardScreeningResult,
    confirmDeleteApplication,
    deleteApplications,
    pageCount,
  } = useCandidateListController(view === "applications");
  const setView = (next: CandidateView) => {
    const params = new URLSearchParams(searchParams);
    if (next === "intakes") params.set("view", "intakes");
    else params.delete("view");
    setSearchParams(params, { replace: true });
  };
  const [intakeUnreadCount, setIntakeUnreadCount] = useState(0);
  const [intakeRefreshVersion, setIntakeRefreshVersion] = useState(0);
  const [timelineTarget, setTimelineTarget] =
    useState<ApplicationTimelineTarget>();
  const [timelineEvents, setTimelineEvents] = useState<
    WorkflowExecutionEvent[]
  >([]);
  const [timelineLoading, setTimelineLoading] = useState(false);
  const [timelineError, setTimelineError] = useState("");
  const [assessmentResult, setAssessmentResult] = useState<AssessmentResultState>();
  const [resumeSourceTarget, setResumeSourceTarget] = useState<ApplicationListItem>();
  const [hardPolicyTarget, setHardPolicyTarget] = useState<ApplicationListItem>();
  const [jobProfileTarget, setJobProfileTarget] = useState<ApplicationListItem>();
  const [assessmentRecoverySubmitting, setAssessmentRecoverySubmitting] = useState(false);
  const [selectedApplicationIds, setSelectedApplicationIds] = useState<Set<string>>(new Set());
  const [bulkDeleteOpen, setBulkDeleteOpen] = useState(false);
  const [bulkDeleteProgress, setBulkDeleteProgress] = useState({ completed: 0, total: 0 });
  const [moreFiltersOpen, setMoreFiltersOpen] = useState(false);
  const [advancedFilterDraft, setAdvancedFilterDraft] = useState<AdvancedFilterDraft>({
    hardScreeningFilter: "all",
    dateRange: "all",
    customDateFrom: "",
    customDateTo: "",
    highestDegree: "",
    majorKeyword: "",
    minimumScore: "",
    overdueOnly: false,
    attentionOnly: false,
    sort: "score_desc",
  });
  const applicationSelectAllRef = useRef<HTMLInputElement>(null);
  const moreFiltersRef = useRef<HTMLDivElement>(null);
  const deletableApplications = applications.filter((item) =>
    item.availableActions.includes("delete_application"),
  );
  const canBulkDeleteApplications = deletableApplications.length > 0;
  const allApplicationsSelected =
    deletableApplications.length > 0 &&
    deletableApplications.every((item) =>
      selectedApplicationIds.has(item.applicationId),
    );
  const someApplicationsSelected = selectedApplicationIds.size > 0 && !allApplicationsSelected;
  const visibleJobs = departmentId
    ? jobs.filter((job) => job.departmentId === departmentId)
    : jobs;
  const scopedDepartmentName = user?.departmentId
    ? departments.find((department) => department.departmentId === user.departmentId)?.name
    : undefined;
  const activeAdvancedFilterCount = [
    hardScreeningFilter !== "all",
    dateRange !== "all",
    Boolean(highestDegree),
    Boolean(majorKeyword.trim()),
    minimumScore !== "",
    overdueOnly,
    attentionOnly,
    sort !== "score_desc",
  ].filter(Boolean).length;
  const draftMinimumScore = Number(advancedFilterDraft.minimumScore);
  const draftMinimumScoreValid =
    advancedFilterDraft.minimumScore === "" ||
    (Number.isFinite(draftMinimumScore) && draftMinimumScore >= 0 && draftMinimumScore <= 100);
  const draftDateValid =
    advancedFilterDraft.dateRange !== "custom" ||
    !advancedFilterDraft.customDateFrom ||
    !advancedFilterDraft.customDateTo ||
    advancedFilterDraft.customDateFrom <= advancedFilterDraft.customDateTo;

  const openMoreFilters = () => {
    setAdvancedFilterDraft({
      hardScreeningFilter,
      dateRange,
      customDateFrom,
      customDateTo,
      highestDegree,
      majorKeyword,
      minimumScore,
      overdueOnly,
      attentionOnly,
      sort,
    });
    setMoreFiltersOpen((current) => !current);
  };

  const applyAdvancedFilters = () => {
    if (!draftMinimumScoreValid || !draftDateValid) return;
    setHardScreeningFilter(advancedFilterDraft.hardScreeningFilter);
    setDateRange(advancedFilterDraft.dateRange);
    setCustomDateFrom(advancedFilterDraft.customDateFrom);
    setCustomDateTo(advancedFilterDraft.customDateTo);
    setHighestDegree(advancedFilterDraft.highestDegree);
    setMajorKeyword(advancedFilterDraft.majorKeyword.trim());
    setMinimumScore(advancedFilterDraft.minimumScore);
    setOverdueOnly(advancedFilterDraft.overdueOnly);
    setAttentionOnly(advancedFilterDraft.attentionOnly);
    setSort(advancedFilterDraft.sort);
    if (advancedFilterDraft.hardScreeningFilter === "failed") {
      setStatus("rejected");
      setApplicationStatus("");
    }
    setPage(1);
    setMoreFiltersOpen(false);
  };

  const resetAdvancedFilters = () => {
    const reset: AdvancedFilterDraft = {
      hardScreeningFilter: "all",
      dateRange: "all",
      customDateFrom: "",
      customDateTo: "",
      highestDegree: "",
      majorKeyword: "",
      minimumScore: "",
      overdueOnly: false,
      attentionOnly: false,
      sort: "score_desc",
    };
    setAdvancedFilterDraft(reset);
    setHardScreeningFilter(reset.hardScreeningFilter);
    setDateRange(reset.dateRange);
    setCustomDateFrom(reset.customDateFrom);
    setCustomDateTo(reset.customDateTo);
    setHighestDegree(reset.highestDegree);
    setMajorKeyword(reset.majorKeyword);
    setMinimumScore(reset.minimumScore);
    setOverdueOnly(reset.overdueOnly);
    setAttentionOnly(reset.attentionOnly);
    setSort(reset.sort);
    setPage(1);
    setMoreFiltersOpen(false);
  };

  useEffect(() => {
    if (applicationSelectAllRef.current) {
      applicationSelectAllRef.current.indeterminate = someApplicationsSelected;
    }
  }, [someApplicationsSelected]);

  useEffect(() => {
    setSelectedApplicationIds(new Set());
  }, [view, keyword, status, departmentId, jobId, applicationStatus, hardScreeningFilter, dateRange, customDateFrom, customDateTo, highestDegree, majorKeyword, minimumScore, overdueOnly, attentionOnly, page, pageSize]);

  useEffect(() => {
    if (!moreFiltersOpen) return;
    const handlePointerDown = (event: PointerEvent) => {
      if (!moreFiltersRef.current?.contains(event.target as Node)) {
        setMoreFiltersOpen(false);
      }
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMoreFiltersOpen(false);
    };
    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [moreFiltersOpen]);

  useEffect(() => {
    if (!token) return;
    void getCandidateIntakeSummary(token)
      .then((summary) => setIntakeUnreadCount(summary.unreadAttentionCount))
      .catch(() => setIntakeUnreadCount(0));
  }, [token]);

  const loadTimeline = useCallback(async () => {
    if (!token || !timelineTarget) return;
    setTimelineLoading(true);
    setTimelineError("");
    try {
      const view = await getApplicationWorkflowTimeline(
        token,
        timelineTarget.applicationId,
        timelineTarget.workflowRunId,
      );
      setTimelineEvents(view.items);
    } catch (reason) {
      setTimelineError(
        reason instanceof Error ? reason.message : "执行轨迹加载失败",
      );
    } finally {
      setTimelineLoading(false);
    }
  }, [timelineTarget, token]);

  useEffect(() => {
    void loadTimeline();
  }, [loadTimeline]);
  useEffect(() => {
    if (!timelineTarget || !isWorkflowProcessActive(timelineTarget.process))
      return;
    const timer = window.setInterval(() => void loadTimeline(), 5000);
    return () => window.clearInterval(timer);
  }, [loadTimeline, timelineTarget]);

  const openAssessmentResult = useCallback(
    async (item: ApplicationListItem, stage: AssessmentStageSummary["stage"]) => {
      if (!token) return;
      setAssessmentResult({ item, stage, loading: true });
      try {
        if (stage === "v1") {
          const view = await getScreeningReviewView(token, item.applicationId);
          setAssessmentResult({ item, stage, loading: false, v1: view });
        } else if (stage === "v2") {
          const view = await getSecondInterviewReviewView(token, item.applicationId);
          setAssessmentResult({ item, stage, loading: false, v2: view });
        } else {
          const view = await getFinalReviewView(token, item.applicationId);
          setAssessmentResult({ item, stage, loading: false, v3: view });
        }
      } catch (reason) {
        setAssessmentResult({
          item,
          stage,
          loading: false,
          error: reason instanceof Error ? reason.message : "评估结果加载失败",
        });
      }
    },
    [token],
  );

  const handleAssessmentRecoveryAction = useCallback(async (
    action: string,
    input?: string,
  ) => {
    const current = assessmentResult;
    if (!current || !current.item) return;
    setAssessmentRecoverySubmitting(true);
    let accepted = false;
    try {
      if (action === "retry_post_first_scoring") {
        accepted = (await retryPostFirstScoring(current.item.applicationId)) !== undefined;
      } else if (action === "retry_post_second_scoring") {
        accepted = (await retryPostSecondScoring(current.item.applicationId)) !== undefined;
      } else if (action === "rebuild_screening_assessment") {
        accepted = (await rebuildScreeningAssessment(current.item.applicationId)) !== undefined;
      } else if (action === "rebuild_previous_post_first_assessment") {
        accepted = (await retryPostFirstScoring(current.item.applicationId)) !== undefined;
      } else if (action === "edit_first_interview_feedback" && input?.trim()) {
        accepted = (await repairPostFirstScoring(current.item.applicationId, input.trim())) !== undefined;
      } else if (action === "edit_second_interview_feedback" && input?.trim()) {
        accepted = (await repairPostSecondScoring(current.item.applicationId, input.trim())) !== undefined;
      }
      if (accepted) {
        await loadApplications();
        await openAssessmentResult(current.item, current.stage);
      }
    } finally {
      setAssessmentRecoverySubmitting(false);
    }
  }, [assessmentResult, loadApplications, openAssessmentResult, rebuildScreeningAssessment, repairPostFirstScoring, repairPostSecondScoring, retryPostFirstScoring, retryPostSecondScoring]);

  const openAssessmentSourceRepair = useCallback((action: string, item: ApplicationListItem) => {
    if (action === "repair_resume_source") {
      setResumeSourceTarget(item);
    } else if (action === "repair_job_profile") {
      setJobProfileTarget(item);
    }
    setAssessmentResult(undefined);
  }, []);
  const hardResultApplication = hardResult
    ? applications.find(
        (item) => item.applicationId === hardResult.applicationId,
      )
    : undefined;

  const requestBulkDeleteApplications = () => {
    if (selectedApplicationIds.size > 0) {
      setBulkDeleteProgress({ completed: 0, total: selectedApplicationIds.size });
      setBulkDeleteOpen(true);
    }
  };

  const confirmBulkDeleteApplications = async () => {
    const ids = Array.from(selectedApplicationIds);
    if (!ids.length || bulkDeleting) return;
    setBulkDeleteProgress({ completed: 0, total: ids.length });
    const result = await deleteApplications(ids, (completed, total) => {
      setBulkDeleteProgress({ completed, total });
    });
    setSelectedApplicationIds(new Set(result.failedIds));
    setBulkDeleteOpen(false);
  };
  return (
    <>
      <PageHeader
        title="候选人列表"
        description="查看候选人进度、评估结果和待办事项。"
      />

      <Section
        title="全部候选人"
        description={`共 ${total} 份申请，按阶段筛选并处理当前待办`}
        action={
          view === "intakes" && hasBusinessPermission(user, "resume.upload") ? (
            <Button
              className="h-9 px-3 text-xs"
              type="button"
              variant="primary"
              onClick={() => setUploadOpen(true)}
            >
              <Upload size={15} />
              上传简历
            </Button>
          ) : undefined
        }
      >
        <div className="mb-5 border-b border-line">
          <div className="flex gap-6" aria-label="候选人视图切换">
            <button
              type="button"
              className={`-mb-px h-10 border-b-2 px-0.5 text-sm font-medium ${view === "applications" ? "border-blue-600 text-blue-700" : "border-transparent text-slate-500"}`}
              onClick={() => setView("applications")}
            >
              招聘流程
            </button>
            <button
              type="button"
              className={`-mb-px flex h-10 items-center gap-2 border-b-2 px-0.5 text-sm font-medium ${view === "intakes" ? "border-blue-600 text-blue-700" : "border-transparent text-slate-500"}`}
              onClick={() => setView("intakes")}
            >
              简历处理
              {intakeUnreadCount > 0 ? (
                <span className="inline-flex min-w-5 items-center justify-center rounded-full bg-rose-600 px-1.5 py-0.5 text-[11px] font-semibold text-white">
                  {intakeUnreadCount > 99 ? "99+" : intakeUnreadCount}
                </span>
              ) : null}
            </button>
          </div>
        </div>
        {view === "intakes" ? (
          <CandidateIntakePanel
            onUnreadCountChange={setIntakeUnreadCount}
            onRequestUpload={() => setUploadOpen(true)}
            refreshVersion={intakeRefreshVersion}
          />
        ) : (
          <>
            <div className="mb-4 border-b border-line">
              <div
                className="flex min-w-max gap-6"
                aria-label="候选人招聘状态筛选"
              >
                {candidateStageOptions.map((option) => {
                  const count = stageCounts[option.value];
                  const selected = status === option.value;
                  return (
                    <button
                      key={option.value}
                      type="button"
                      aria-pressed={selected}
                      className={`relative -mb-px flex h-10 items-center gap-2 border-b-2 px-0.5 text-sm font-medium transition ${selected ? "border-blue-600 text-blue-700" : "border-transparent text-slate-500 hover:border-slate-300 hover:text-slate-800"}`}
                      onClick={() => {
                        setStatus(option.value);
                        setApplicationStatus("");
                        if (
                          hardScreeningFilter === "failed" &&
                          option.value !== "rejected"
                        ) {
                          setHardScreeningFilter("all");
                        }
                        setPage(1);
                      }}
                    >
                      <span>{option.label}</span>
                      <span
                        className={`inline-flex min-w-5 items-center justify-center rounded-full px-1.5 py-0.5 text-[11px] font-semibold ${selected ? "bg-blue-100 text-blue-700" : count === 0 ? "bg-slate-100 text-slate-400" : "bg-slate-200 text-slate-600"}`}
                      >
                        {count}
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
            <div ref={moreFiltersRef} className="relative mb-4">
              <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-[minmax(260px,1fr)_180px_240px_210px_auto]">
                <label className="relative md:col-span-2 xl:col-span-1">
                  <Search
                    className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400"
                    size={16}
                  />
                  <input
                    className="h-9 w-full rounded-md border border-slate-300 bg-white pl-9 pr-3 text-sm outline-none transition focus:border-blue-400 focus:ring-2 focus:ring-blue-100"
                    placeholder="搜索候选人、岗位、部门或编号"
                    value={keyword}
                    onChange={(event) => setKeyword(event.target.value)}
                  />
                </label>
                <select
                  aria-label="部门筛选"
                  className="h-9 min-w-0 rounded-md border border-slate-300 bg-white px-2.5 text-sm disabled:bg-slate-50 disabled:text-slate-500"
                  value={user?.businessScope === "department" ? "" : departmentId}
                  disabled={user?.businessScope === "department"}
                  onChange={(event) => {
                    const nextDepartmentId = event.target.value;
                    setDepartmentId(nextDepartmentId);
                    const selectedJob = jobs.find((job) => job.jobId === jobId);
                    if (
                      selectedJob &&
                      nextDepartmentId &&
                      selectedJob.departmentId !== nextDepartmentId
                    ) {
                      setJobId("");
                    }
                    setPage(1);
                  }}
                >
                  <option value="">
                    {user?.businessScope === "department"
                      ? scopedDepartmentName || "本部门"
                      : "全部部门"}
                  </option>
                  {user?.businessScope === "organization"
                    ? departments.map((department) => (
                        <option
                          key={department.departmentId}
                          value={department.departmentId}
                        >
                          {department.name}
                        </option>
                      ))
                    : null}
                </select>
                <select
                  aria-label="岗位筛选"
                  className="h-9 min-w-0 rounded-md border border-slate-300 bg-white px-2.5 text-sm"
                  value={jobId}
                  onChange={(event) => {
                    setJobId(event.target.value);
                    setPage(1);
                  }}
                >
                  <option value="">
                    {departmentId ? "该部门全部岗位" : "全部岗位"}
                  </option>
                  {visibleJobs.map((job) => (
                    <option key={job.jobId} value={job.jobId}>
                      {departmentId ? job.title : `${job.title} · ${job.departmentName}`}
                    </option>
                  ))}
                </select>
                <select
                  aria-label="当前流程阶段筛选"
                  className="h-9 min-w-0 rounded-md border border-slate-300 bg-white px-2.5 text-sm disabled:bg-slate-50 disabled:text-slate-400"
                  value={applicationStatus}
                  disabled={status !== "in_progress"}
                  onChange={(event) => {
                    setApplicationStatus(event.target.value);
                    setPage(1);
                  }}
                >
                  <option value="">
                    {status === "in_progress" ? "全部流程阶段" : "当前结果无需阶段筛选"}
                  </option>
                  {status === "in_progress"
                    ? applicationStageGroups.map((group) => (
                        <optgroup key={group.label} label={group.label}>
                          {group.options.map((option) => (
                            <option key={option.value} value={option.value}>
                              {option.label}
                            </option>
                          ))}
                        </optgroup>
                      ))
                    : null}
                </select>
                <Button
                  type="button"
                  className="relative whitespace-nowrap px-3"
                  aria-expanded={moreFiltersOpen}
                  aria-controls="application-more-filters"
                  onClick={openMoreFilters}
                >
                  <SlidersHorizontal size={15} />
                  更多筛选
                  {activeAdvancedFilterCount > 0 ? (
                    <span className="inline-flex min-w-5 items-center justify-center rounded-full bg-blue-600 px-1.5 py-0.5 text-[11px] font-semibold text-white">
                      {activeAdvancedFilterCount}
                    </span>
                  ) : null}
                </Button>
              </div>

              {activeAdvancedFilterCount > 0 ? (
                <div className="mt-2 flex min-h-8 flex-wrap items-center gap-2">
                  {hardScreeningFilter !== "all" ? (
                    <FilterChip
                      label={`硬筛：${hardScreeningFilterLabels[hardScreeningFilter]}`}
                      onRemove={() => {
                        setHardScreeningFilter("all");
                        setPage(1);
                      }}
                    />
                  ) : null}
                  {dateRange !== "all" ? (
                    <FilterChip
                      label={`投递时间：${dateRangeLabels[dateRange]}`}
                      onRemove={() => {
                        setDateRange("all");
                        setCustomDateFrom("");
                        setCustomDateTo("");
                        setPage(1);
                      }}
                    />
                  ) : null}
                  {highestDegree ? (
                    <FilterChip
                      label={`学历：${highestDegree}`}
                      onRemove={() => {
                        setHighestDegree("");
                        setPage(1);
                      }}
                    />
                  ) : null}
                  {majorKeyword ? (
                    <FilterChip
                      label={`专业：${majorKeyword}`}
                      onRemove={() => {
                        setMajorKeyword("");
                        setPage(1);
                      }}
                    />
                  ) : null}
                  {minimumScore !== "" ? (
                    <FilterChip
                      label={`能力分 ≥ ${minimumScore}`}
                      onRemove={() => {
                        setMinimumScore("");
                        setPage(1);
                      }}
                    />
                  ) : null}
                  {overdueOnly ? (
                    <FilterChip
                      label="仅看逾期"
                      onRemove={() => {
                        setOverdueOnly(false);
                        setPage(1);
                      }}
                    />
                  ) : null}
                  {attentionOnly ? (
                    <FilterChip
                      label="仅看异常/需确认"
                      onRemove={() => {
                        setAttentionOnly(false);
                        setPage(1);
                      }}
                    />
                  ) : null}
                  {sort !== "score_desc" ? (
                    <FilterChip
                      label={`排序：${sortLabels[sort]}`}
                      onRemove={() => {
                        setSort("score_desc");
                        setPage(1);
                      }}
                    />
                  ) : null}
                  <button
                    type="button"
                    className="h-7 px-1 text-xs text-slate-500 hover:text-slate-800"
                    onClick={resetAdvancedFilters}
                  >
                    清除全部
                  </button>
                </div>
              ) : null}

              {moreFiltersOpen ? (
                <div
                  id="application-more-filters"
                  className="absolute right-0 top-11 z-30 w-full max-w-xl rounded-md border border-slate-200 bg-white p-4 shadow-xl shadow-slate-900/10"
                >
                  <div className="mb-4 flex items-center justify-between gap-3">
                    <h3 className="text-sm font-semibold text-ink">更多筛选</h3>
                    <button
                      type="button"
                      className="grid size-8 place-items-center rounded-md text-slate-500 hover:bg-slate-100 hover:text-slate-800"
                      aria-label="关闭更多筛选"
                      onClick={() => setMoreFiltersOpen(false)}
                    >
                      <X size={16} />
                    </button>
                  </div>
                  <div className="grid gap-3 sm:grid-cols-2">
                    <FilterField label="硬筛结果">
                      <select
                        aria-label="硬筛结果"
                        className="h-9 w-full rounded-md border border-slate-300 bg-white px-2.5 text-sm"
                        value={advancedFilterDraft.hardScreeningFilter}
                        onChange={(event) =>
                          setAdvancedFilterDraft((current) => ({
                            ...current,
                            hardScreeningFilter: event.target.value as HardScreeningListFilter,
                          }))
                        }
                      >
                        <option value="all">全部硬筛结果</option>
                        <option value="passed">硬筛已通过</option>
                        <option value="failed">硬筛未通过</option>
                        <option value="manual_review">硬筛待复核</option>
                        <option value="processing">硬筛处理中</option>
                        <option value="not_configured">硬筛未启用</option>
                      </select>
                    </FilterField>
                    <FilterField label="投递时间">
                      <select
                        aria-label="投递时间"
                        className="h-9 w-full rounded-md border border-slate-300 bg-white px-2.5 text-sm"
                        value={advancedFilterDraft.dateRange}
                        onChange={(event) =>
                          setAdvancedFilterDraft((current) => ({
                            ...current,
                            dateRange: event.target.value as SubmittedDateRange,
                          }))
                        }
                      >
                        <option value="all">全部时间</option>
                        <option value="today">今天</option>
                        <option value="3d">最近3天</option>
                        <option value="7d">最近7天</option>
                        <option value="30d">最近30天</option>
                        <option value="this_month">本月</option>
                        <option value="90d">最近90天</option>
                        <option value="custom">自定义范围</option>
                      </select>
                    </FilterField>
                    {advancedFilterDraft.dateRange === "custom" ? (
                      <div className="grid gap-2 sm:col-span-2 sm:grid-cols-2">
                        <FilterField label="开始日期">
                          <input
                            aria-label="开始日期"
                            type="date"
                            className="h-9 w-full rounded-md border border-slate-300 bg-white px-2.5 text-sm"
                            value={advancedFilterDraft.customDateFrom}
                            max={advancedFilterDraft.customDateTo || undefined}
                            onChange={(event) =>
                              setAdvancedFilterDraft((current) => ({
                                ...current,
                                customDateFrom: event.target.value,
                              }))
                            }
                          />
                        </FilterField>
                        <FilterField label="结束日期">
                          <input
                            aria-label="结束日期"
                            type="date"
                            className="h-9 w-full rounded-md border border-slate-300 bg-white px-2.5 text-sm"
                            value={advancedFilterDraft.customDateTo}
                            min={advancedFilterDraft.customDateFrom || undefined}
                            onChange={(event) =>
                              setAdvancedFilterDraft((current) => ({
                                ...current,
                                customDateTo: event.target.value,
                              }))
                            }
                          />
                        </FilterField>
                      </div>
                    ) : null}
                    <FilterField label="学历">
                      <select
                        aria-label="学历"
                        className="h-9 w-full rounded-md border border-slate-300 bg-white px-2.5 text-sm"
                        value={advancedFilterDraft.highestDegree}
                        onChange={(event) =>
                          setAdvancedFilterDraft((current) => ({
                            ...current,
                            highestDegree: event.target.value,
                          }))
                        }
                      >
                        <option value="">全部学历</option>
                        <option value="大专">大专</option>
                        <option value="本科">本科</option>
                        <option value="硕士">硕士</option>
                        <option value="博士">博士</option>
                        <option value="其他">其他</option>
                      </select>
                    </FilterField>
                    <FilterField label="专业">
                      <input
                        aria-label="专业"
                        className="h-9 w-full rounded-md border border-slate-300 bg-white px-2.5 text-sm"
                        placeholder="输入专业关键词"
                        value={advancedFilterDraft.majorKeyword}
                        onChange={(event) =>
                          setAdvancedFilterDraft((current) => ({
                            ...current,
                            majorKeyword: event.target.value,
                          }))
                        }
                      />
                    </FilterField>
                    <FilterField label="最低能力分">
                      <input
                        aria-label="最低能力分"
                        type="number"
                        min="0"
                        max="100"
                        step="0.1"
                        className="h-9 w-full rounded-md border border-slate-300 bg-white px-2.5 text-sm"
                        placeholder="例如 70"
                        value={advancedFilterDraft.minimumScore}
                        onChange={(event) =>
                          setAdvancedFilterDraft((current) => ({
                            ...current,
                            minimumScore: event.target.value,
                          }))
                        }
                      />
                      {!draftMinimumScoreValid ? (
                        <span className="text-xs text-rose-600">请输入 0 到 100 之间的分数</span>
                      ) : null}
                    </FilterField>
                    <FilterField label="排序">
                      <SortSelect
                        aria-label="排序"
                        className="w-full"
                        value={advancedFilterDraft.sort}
                        onChange={(event) =>
                          setAdvancedFilterDraft((current) => ({
                            ...current,
                            sort: event.target.value as ApplicationListSort,
                          }))
                        }
                      >
                        <option value="submitted_desc">最新投递</option>
                        <option value="submitted_asc">最早投递</option>
                        <option value="score_desc">评分从高到低</option>
                        <option value="score_asc">评分从低到高</option>
                      </SortSelect>
                    </FilterField>
                  </div>
                  <div className="mt-4 flex flex-wrap gap-x-5 gap-y-2 text-sm text-slate-700">
                    <label className="inline-flex items-center gap-2">
                      <input
                        type="checkbox"
                        checked={advancedFilterDraft.overdueOnly}
                        onChange={(event) =>
                          setAdvancedFilterDraft((current) => ({
                            ...current,
                            overdueOnly: event.target.checked,
                          }))
                        }
                      />
                      仅看逾期
                    </label>
                    <label className="inline-flex items-center gap-2">
                      <input
                        type="checkbox"
                        checked={advancedFilterDraft.attentionOnly}
                        onChange={(event) =>
                          setAdvancedFilterDraft((current) => ({
                            ...current,
                            attentionOnly: event.target.checked,
                          }))
                        }
                      />
                      仅看异常/需确认
                    </label>
                  </div>
                  {!draftDateValid ? (
                    <p className="mt-3 text-xs text-rose-600">
                      自定义时间的开始日期不能晚于结束日期
                    </p>
                  ) : null}
                  <div className="mt-4 flex justify-end gap-2 border-t border-line pt-4">
                    <Button type="button" onClick={resetAdvancedFilters}>
                      重置
                    </Button>
                    <Button
                      type="button"
                      variant="primary"
                      disabled={!draftMinimumScoreValid || !draftDateValid}
                      onClick={applyAdvancedFilters}
                    >
                      应用筛选
                    </Button>
                  </div>
                </div>
              ) : null}
            </div>
            {canBulkDeleteApplications && selectedApplicationIds.size > 0 ? (
              <div className="mb-3 flex flex-wrap items-center justify-between gap-3 rounded-md border border-blue-200 bg-blue-50 px-3 py-2.5">
                <span className="text-sm font-medium text-blue-900">
                  已选择 {selectedApplicationIds.size} 份申请
                </span>
                <div className="flex items-center gap-2">
                  <Button
                    className="h-8 px-2.5 text-xs"
                    onClick={() => setSelectedApplicationIds(new Set())}
                    disabled={bulkDeleting}
                  >
                    清除选择
                  </Button>
                  <Button
                    className="h-8 px-2.5 text-xs"
                    variant="danger"
                    onClick={requestBulkDeleteApplications}
                    disabled={bulkDeleting}
                  >
                    <Trash2 size={14} />
                    删除所选
                  </Button>
                </div>
              </div>
            ) : null}
            {listError ? (
              <p className="mb-3 rounded-md bg-rose-50 p-3 text-sm text-rose-700">
                {listError}
              </p>
            ) : null}
            <div className="overflow-x-auto rounded-md border border-line">
              <table className="data-table data-table-fixed w-full min-w-[1504px] border-collapse text-left text-sm">
                <colgroup>
                  {canBulkDeleteApplications ? <col className="w-[44px]" /> : null}
                  <col className="w-[190px]" />
                  <col className="w-[190px]" />
                  <col className="w-[210px]" />
                  <col className="w-[170px]" />
                  <col className="w-[220px]" />
                  <col className="w-[100px]" />
                  <col className="w-[240px]" />
                  <col className="w-[140px]" />
                </colgroup>
                <thead className="bg-slate-50 text-xs font-medium text-slate-500">
                  <tr>
                    {canBulkDeleteApplications ? (
                      <th className="px-3 py-2.5">
                        <input
                          ref={applicationSelectAllRef}
                          type="checkbox"
                          aria-label="选择当前页全部申请"
                          checked={allApplicationsSelected}
                          disabled={bulkDeleting}
                          onChange={(event) => {
                            setSelectedApplicationIds(
                              event.target.checked
                                ? new Set(
                                    deletableApplications.map(
                                      (item) => item.applicationId,
                                    ),
                                  )
                                : new Set(),
                            );
                          }}
                        />
                      </th>
                    ) : null}
                    <th className="px-4 py-2.5 font-medium">候选人</th>
                    <th className="px-4 py-2.5 font-medium">学历</th>
                    <th className="px-4 py-2.5 font-medium">岗位/部门</th>
                    <th className="px-4 py-2.5 font-medium">资料状态</th>
                    <th className="px-4 py-2.5 font-medium">当前阶段</th>
                    <th className="data-table-number px-4 py-2.5">能力分</th>
                    <th className="px-4 py-2.5 font-medium">
                      <span className="inline-flex items-center gap-1">
                        硬筛
                        <span className="group relative inline-flex">
                          <button
                            type="button"
                            aria-label="查看硬筛结果说明"
                            aria-describedby="hard-screening-history-help"
                            className="rounded-full text-slate-400 outline-none transition hover:text-blue-700 focus-visible:ring-2 focus-visible:ring-blue-300"
                          >
                            <HelpCircle size={14} />
                          </button>
                          <span
                            id="hard-screening-history-help"
                            role="tooltip"
                            className="pointer-events-none absolute left-1/2 top-6 z-30 hidden w-72 -translate-x-1/2 rounded-md border border-slate-200 bg-slate-900 px-3 py-2 text-left text-xs font-normal leading-5 text-white shadow-lg group-hover:block group-focus-within:block"
                          >
                            硬筛结果按候选人投递时生效的规则保存。岗位之后新增、修改或停用硬筛规则，不会改变该候选人的历史结果。
                          </span>
                        </span>
                      </span>
                    </th>
                    <th className="data-table-action px-4 py-2.5">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {applications.map((item) => (
                    <tr
                      key={item.applicationId}
                      className="border-t border-line transition hover:bg-slate-50/70"
                    >
                      {canBulkDeleteApplications ? (
                        <td className="px-3 py-3 align-top">
                          <input
                            type="checkbox"
                            aria-label={`选择${item.candidateName}的${item.jobTitle}申请`}
                            checked={selectedApplicationIds.has(item.applicationId)}
                            disabled={
                              bulkDeleting ||
                              !item.availableActions.includes("delete_application")
                            }
                            onChange={(event) => {
                              setSelectedApplicationIds((current) => {
                                const next = new Set(current);
                                if (event.target.checked) next.add(item.applicationId);
                                else next.delete(item.applicationId);
                                return next;
                              });
                            }}
                          />
                        </td>
                      ) : null}
                      <td className="px-4 py-3">
                        <div
                          className={`truncate font-medium ${item.candidateName === "待确认候选人" ? "text-amber-700" : "text-ink"}`}
                        >
                          {item.candidateName === "待确认候选人"
                            ? "姓名待确认"
                            : item.candidateName}
                        </div>
                        <div className="data-table-secondary">
                          {item.candidateResolved
                            ? item.currentTitle || "候选人"
                            : item.processingStage || "信息识别中"}
                        </div>
                        {formatExperience(item.yearsOfExperience) ? (
                          <div className="mt-1 text-xs text-muted">
                            {formatExperience(item.yearsOfExperience)}
                          </div>
                        ) : null}
                        {item.resumePdfUrl ? (
                          <AuthenticatedPdfLink url={item.resumePdfUrl} />
                        ) : null}
                      </td>
                      <td className="px-4 py-3">
                        <div className="truncate">
                          {item.candidateResolved
                            ? item.school || "—"
                            : "信息识别中"}
                        </div>
                        <div className="data-table-secondary">
                          {item.candidateResolved
                            ? [item.highestDegree, item.major]
                                .filter(Boolean)
                                .join(" · ") || "—"
                            : ""}
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <div className="truncate">{item.jobTitle}</div>
                        <div className="data-table-secondary">
                          {item.department || "部门未配置"}
                        </div>
                        {item.jobConfigurationStatus === "incomplete" ? (
                          <Badge className="mt-1 border-amber-300 bg-amber-50 text-amber-800">
                            {jobConfigurationLabel(item.missingJobAssignments)}
                          </Badge>
                        ) : null}
                        <div
                          className="data-table-secondary truncate"
                          title={item.jobMajorRequirement || "未设置"}
                        >
                          专业要求：{item.jobMajorRequirement || "未设置"}
                        </div>
                        <div className="data-table-secondary">
                          投递于 {formatDateTime(item.submittedAt)}
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <DocumentStatusBadge status={item.documentStatus} />
                        {item.resumeFilename ? (
                          <div className="mt-1 max-w-44 truncate text-xs text-muted">
                            {item.resumeFilename}
                          </div>
                        ) : null}
                      </td>
                      <td className="px-4 py-3">
                        <Badge
                          className={
                            item.preScreeningProcess
                              ? preScreeningTones[item.preScreeningProcess.tone]
                              : statusTone[item.status]
                          }
                        >
                          {item.preScreeningProcess?.label ?? statusLabels[item.status]}
                        </Badge>
                        {item.preScreeningProcess?.message ? (
                          <div
                            className={`mt-1 max-w-56 text-xs leading-5 ${
                              item.preScreeningProcess.tone === "danger"
                                ? "text-rose-700"
                                : item.preScreeningProcess.tone === "warning"
                                  ? "text-amber-800"
                                  : item.preScreeningProcess.tone === "info"
                                    ? "text-blue-700"
                                    : "text-muted"
                            }`}
                          >
                            {item.preScreeningProcess.message}
                          </div>
                        ) : null}
                        {/* 简历重建是候选人级辅助状态，不改变 Application.status，只在当前招聘阶段下方提示。 */}
                        {item.resumeRebuildProcess ? (
                          <div className="mt-1">
                            <WorkflowProcessStatus
                              process={item.resumeRebuildProcess}
                              compact
                            />
                          </div>
                        ) : item.resumeRebuildStatus !== "idle" ? (
                          <div className="mt-1 max-w-56 text-xs leading-5 text-muted">
                            ·{" "}
                            {item.resumeRebuildMessage ||
                              "简历重建状态正在更新"}
                          </div>
                        ) : null}
                        {item.processingStage ? (
                          <div className="mt-1 max-w-56 text-xs leading-5 text-blue-700">
                            {item.processingStage}
                          </div>
                        ) : null}
                        {item.processingError ? (
                          <div className="mt-1 max-w-56 text-xs leading-5 text-rose-700">
                            {toUserFacingError(item.processingError)}
                          </div>
                        ) : null}
                        {item.assessmentUpdateProcess ? (
                          <div className="mt-1">
                            <WorkflowProcessStatus
                              process={item.assessmentUpdateProcess}
                              compact
                            />
                          </div>
                        ) : null}
                        {item.assessmentUpdateStatus !== "idle" ? (
                          <div
                            className={`mt-1 max-w-56 text-xs leading-5 ${item.assessmentUpdateStatus === "failed" ? "text-rose-700" : item.assessmentUpdateStatus === "review_required" ? "text-amber-700" : item.assessmentUpdateStatus === "completed" ? "text-emerald-700" : "text-blue-700"}`}
                          >
                            ·{" "}
                            {item.assessmentUpdateStage === "first"
                              ? "一面后评估"
                              : "二面后评估"}
                            {item.assessmentUpdateStatus === "queued"
                              ? "排队中"
                              : item.assessmentUpdateStatus === "running"
                                ? "进行中"
                                : item.assessmentUpdateStatus ===
                                    "review_required"
                                  ? "待确认"
                                  : item.assessmentUpdateStatus === "failed"
                                    ? "失败"
                                    : "已完成"}
                          </div>
                        ) : null}

                        {item.status === "closed_rejected" &&
                        item.rejectionStage ? (
                          <div className="mt-1 text-xs text-muted">
                            终止于：{rejectionStageLabels[item.rejectionStage]}
                          </div>
                        ) : null}
                        {item.screeningError ? (
                          <div
                            className="mt-1 line-clamp-2 text-xs leading-5 text-rose-700"
                            title={toUserFacingError(item.screeningError)}
                          >
                            {toUserFacingError(item.screeningError)}
                          </div>
                        ) : null}
                      </td>
                      <td className="data-table-number px-4 py-3">
                        <div className="font-semibold">
                          {item.scoreStatus === "pending" ||
                          item.currentScore == null
                            ? "待评分"
                            : item.currentScore.toFixed(1)}
                        </div>
                        {item.assessmentUpdateProcess &&
                        [
                          "queued",
                          "running",
                          "retry_wait",
                          "waiting_external",
                          "blocked",
                        ].includes(
                          item.assessmentUpdateProcess.processStatus,
                        ) ? (
                          <div className="mt-1 text-xs text-muted">
                            当前显示上一阶段分数
                          </div>
                        ) : null}
                      </td>
                      <td className="px-4 py-3">
                        <HardScreeningCellSummary item={item} />
                      </td>
                      <td className="data-table-action px-4 py-3">
                        <div className="flex items-center justify-end gap-1">
                          <button
                            type="button"
                            className={`inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md transition ${
                              actionMenu?.item.applicationId ===
                                item.applicationId
                                  ? "bg-slate-100 text-slate-800"
                                  : "text-slate-500 hover:bg-slate-100 hover:text-slate-800"
                            }`}
                            aria-label={`${item.candidateName} 的更多操作`}
                            aria-haspopup="menu"
                            aria-expanded={
                              actionMenu?.item.applicationId ===
                                item.applicationId
                            }
                            title="更多操作"
                            data-candidate-action-trigger
                            onClick={(event) => {
                                if (
                                  actionMenu?.item.applicationId ===
                                  item.applicationId
                                ) {
                                  setActionMenu(undefined);
                                  return;
                                }
                                const rect =
                                  event.currentTarget.getBoundingClientRect();
                                const menuWidth = 208;
                                // 该菜单可能同时包含轨迹、重试、工作台和文件等多项操作。
                                // 预留完整菜单高度，底部不足时向上展开，确保所有项均可点击。
                                const menuHeight = 440;
                                const availableBelow =
                                  window.innerHeight - rect.bottom - 12;
                                const availableAbove = rect.top - 12;
                                const placement =
                                  availableBelow >= menuHeight ||
                                  availableBelow >= availableAbove
                                    ? "below"
                                    : "above";
                                // 向上展开以按钮上沿为锚点，并由 CSS 用真实菜单高度向上平移；
                                // 不再把预估高度直接计入坐标，避免菜单与按钮相距很远。
                                setActionMenu({
                                  item,
                                  top:
                                    placement === "below"
                                      ? rect.bottom + 4
                                      : rect.top - 4,
                                  left: Math.max(
                                    8,
                                    Math.min(
                                      rect.right - menuWidth,
                                      window.innerWidth - menuWidth - 8,
                                    ),
                                  ),
                                  placement,
                                  maxHeight: Math.max(
                                    48,
                                    placement === "below"
                                      ? availableBelow
                                      : availableAbove,
                                  ),
                                });
                            }}
                          >
                              <Ellipsis size={17} />
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                  {!loading && applications.length === 0 ? (
                    <tr>
                      <td
                        className="px-4 py-8 text-center text-sm text-muted"
                          colSpan={canBulkDeleteApplications ? 9 : 8}
                      >
                        没有匹配的候选人。
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
            {actionMenu ? (
              <div
                ref={actionMenuRef}
                role="menu"
                aria-label={`${actionMenu.item.candidateName} 的操作`}
                className="fixed z-40 max-h-[calc(100vh-16px)] w-52 overflow-y-auto rounded-lg border border-slate-200 bg-white p-1.5 shadow-lg shadow-slate-900/10"
                style={{
                  top: actionMenu.top,
                  left: actionMenu.left,
                  maxHeight: actionMenu.maxHeight,
                  transform:
                    actionMenu.placement === "above"
                      ? "translateY(-100%)"
                      : undefined,
                }}
              >
                {timelineEntries(actionMenu.item).map((entry) => (
                  <button
                    key={entry.process.workflowRunId}
                    type="button"
                    role="menuitem"
                    className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm text-slate-700 transition hover:bg-slate-50 hover:text-slate-900"
                    onClick={() => {
                      setTimelineTarget({
                        applicationId: actionMenu.item.applicationId,
                        candidateName: actionMenu.item.candidateName,
                        workflowRunId: entry.process.workflowRunId,
                        label: entry.label.replace(/^查看/, ""),
                        process: entry.process,
                      });
                      setActionMenu(undefined);
                    }}
                  >
                    <Eye size={15} />
                    {entry.label}
                  </button>
                ))}
                {(actionMenu.item.assessmentStages ?? [])
                  .filter((stage) =>
                    stage.resultAvailable &&
                    stage.viewAction &&
                    !(actionMenu.item.workspaceAction?.action === "open_post_first_review" && stage.stage === "v2") &&
                    !(actionMenu.item.workspaceAction?.action === "open_final_review" && stage.stage === "v3")
                  )
                  .map((stage) => (
                    <button
                      key={stage.stage}
                      type="button"
                      onClick={() => {
                        void openAssessmentResult(actionMenu.item, stage.stage);
                        setActionMenu(undefined);
                      }}
                      className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm text-slate-700 transition hover:bg-slate-50 hover:text-slate-900"
                      role="menuitem"
                    >
                      <Eye size={15} />
                      {stage.viewAction!.label}
                    </button>
                  ))}
                {actionMenu.item.workspaceAction?.action === "open_post_first_review" ||
                actionMenu.item.workspaceAction?.action === "open_final_review" ? (
                  <button
                    type="button"
                    role="menuitem"
                    className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm text-slate-700 transition hover:bg-slate-50 hover:text-slate-900"
                    onClick={() => {
                      void openAssessmentResult(
                        actionMenu.item,
                        actionMenu.item.workspaceAction?.action === "open_post_first_review" ? "v2" : "v3",
                      );
                      setActionMenu(undefined);
                    }}
                  >
                    <Eye size={15} />
                    {actionMenu.item.workspaceAction.label}
                  </button>
                ) : null}
                {actionMenu.item.hardScreeningStatus !== "not_configured" ? (
                  <button
                    type="button"
                    role="menuitem"
                    className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm text-slate-700 transition hover:bg-slate-50 hover:text-slate-900"
                    onClick={() => {
                      void showHardScreeningResult(
                        actionMenu.item.applicationId,
                      );
                      setActionMenu(undefined);
                    }}
                  >
                    <Eye size={15} />
                    查看硬筛结果
                  </button>
                ) : null}
                {hasRecoveryAction(actionMenu.item.recoveryActions, "run_scoring") ? (
                  <button
                    type="button"
                    role="menuitem"
                    disabled={scoringIds.has(actionMenu.item.applicationId)}
                    className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm text-slate-700 transition hover:bg-slate-50 hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-45"
                    onClick={() => {
                      void handleRunScoring(actionMenu.item.applicationId);
                      setActionMenu(undefined);
                    }}
                  >
                    <RefreshCw size={15} />
                    {scoringIds.has(actionMenu.item.applicationId)
                      ? "重试中"
                      : recoveryAction(actionMenu.item.recoveryActions, "run_scoring")?.label}
                  </button>
                ) : null}
                {hasRecoveryAction(
                  actionMenu.item.recoveryActions,
                  "retry_initial_assessment",
                ) ? (
                  <button
                    type="button"
                    role="menuitem"
                    disabled={initialAssessmentRetryIds.has(actionMenu.item.applicationId)}
                    className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm text-slate-700 transition hover:bg-slate-50 hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-45"
                    onClick={() => {
                      void handleRetryInitialAssessment(actionMenu.item.applicationId);
                      setActionMenu(undefined);
                    }}
                  >
                    <RefreshCw size={15} />
                    {initialAssessmentRetryIds.has(actionMenu.item.applicationId)
                      ? "重试提交中"
                      : recoveryAction(
                          actionMenu.item.recoveryActions,
                          "retry_initial_assessment",
                        )?.label}
                  </button>
                ) : null}
                {actionMenu.item.jobConfigurationStatus === "incomplete" &&
                actionMenu.item.canConfigureJob ? (
                  <Link
                    to="/admin?section=organization"
                    onClick={() => setActionMenu(undefined)}
                  >
                    <button
                      type="button"
                      role="menuitem"
                      className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm text-slate-700 transition hover:bg-slate-50 hover:text-slate-900"
                    >
                      <Settings size={15} />
                      配置岗位负责人
                    </button>
                  </Link>
                ) : null}
                {hasRecoveryAction(
                  actionMenu.item.recoveryActions,
                  "retry_hard_screening",
                ) ? (
                  <button
                    type="button"
                    role="menuitem"
                    disabled={hardScreeningRetryIds.has(
                      actionMenu.item.applicationId,
                    )}
                    className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm text-slate-700 transition hover:bg-slate-50 hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-45"
                    onClick={() => {
                      void handleRetryHardScreening(
                        actionMenu.item.applicationId,
                      );
                      setActionMenu(undefined);
                    }}
                  >
                    <RefreshCw size={15} />
                    {hardScreeningRetryIds.has(
                      actionMenu.item.applicationId,
                    )
                      ? "重试提交中"
                      : recoveryAction(
                          actionMenu.item.recoveryActions,
                          "retry_hard_screening",
                        )?.label}
                  </button>
                ) : null}
                {(
                  hasRecoveryAction(actionMenu.item.recoveryActions, "review_hard_screening_pass") ||
                  hasRecoveryAction(actionMenu.item.recoveryActions, "review_hard_screening_reject")
                ) ? (
                  <button
                    type="button"
                    role="menuitem"
                    className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm text-slate-700 transition hover:bg-slate-50 hover:text-slate-900"
                    onClick={() => {
                      void showHardScreeningResult(
                        actionMenu.item.applicationId,
                      );
                      setActionMenu(undefined);
                    }}
                  >
                    人工处理硬筛
                  </button>
                ) : null}
                {hasRecoveryAction(
                  actionMenu.item.recoveryActions,
                  "repair_resume_source",
                ) ? (
                  <button
                    type="button"
                    role="menuitem"
                    className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm text-slate-700 transition hover:bg-slate-50 hover:text-slate-900"
                    onClick={() => {
                      setResumeSourceTarget(actionMenu.item);
                      setActionMenu(undefined);
                    }}
                  >
                    <FileText size={15} />
                    {recoveryAction(
                      actionMenu.item.recoveryActions,
                      "repair_resume_source",
                    )?.label}
                  </button>
                ) : null}
                {hasRecoveryAction(
                  actionMenu.item.recoveryActions,
                  "repair_job_profile",
                ) ? (
                  <button
                    type="button"
                    role="menuitem"
                    disabled={jobProfileRepairIds.has(actionMenu.item.applicationId)}
                    className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm text-slate-700 transition hover:bg-slate-50 hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-45"
                    onClick={() => {
                      setJobProfileTarget(actionMenu.item);
                      setActionMenu(undefined);
                    }}
                  >
                    <FolderOpen size={15} />
                    {jobProfileRepairIds.has(actionMenu.item.applicationId)
                      ? "岗位来源处理中"
                      : "查看并处理岗位来源"}
                  </button>
                ) : null}
                {hasRecoveryAction(
                  actionMenu.item.recoveryActions,
                  "repair_hard_screening_policy",
                ) ? (
                  <button
                    type="button"
                    role="menuitem"
                    className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm text-slate-700 transition hover:bg-slate-50 hover:text-slate-900"
                    onClick={() => {
                      setHardPolicyTarget(actionMenu.item);
                      setActionMenu(undefined);
                    }}
                  >
                    <Settings size={15} />处理硬筛条件
                  </button>
                ) : null}
                {actionMenu.item.workspaceAction &&
                !["open_post_first_review", "open_final_review"].includes(
                  actionMenu.item.workspaceAction.action,
                ) ? (
                  <Link
                    to={actionMenu.item.workspaceAction.route}
                    state={workspaceEntryState(location.pathname, location.search)}
                    onClick={() => setActionMenu(undefined)}
                  >
                    <button
                      type="button"
                      role="menuitem"
                      className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm text-slate-700 transition hover:bg-slate-50 hover:text-slate-900"
                    >
                      <Eye size={15} />
                      {actionMenu.item.workspaceAction.label}
                    </button>
                  </Link>
                ) : null}
                <button
                  ref={actionMenuItemRef}
                  type="button"
                  role="menuitem"
                  className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm text-slate-700 outline-none transition hover:bg-slate-50 hover:text-slate-900 focus-visible:ring-2 focus-visible:ring-blue-500/40"
                  onClick={() => {
                    setDocumentTarget(actionMenu.item);
                    setActionMenu(undefined);
                  }}
                >
                  <FolderOpen size={15} aria-hidden="true" />
                                查看材料
                </button>
                {actionMenu.item.availableActions.includes("delete_application") ? (
                  <>
                    <div className="my-1 border-t border-slate-100" />
                    <button
                      type="button"
                      role="menuitem"
                      className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm text-slate-700 outline-none transition hover:bg-rose-50 hover:text-rose-600 focus-visible:ring-2 focus-visible:ring-blue-500/40"
                      onClick={() => {
                        setDeleteTarget(actionMenu.item);
                        setActionMenu(undefined);
                      }}
                    >
                      <Trash2 size={15} aria-hidden="true" />
                      删除候选人申请
                    </button>
                  </>
                ) : null}
              </div>
            ) : null}
            <div className="mt-3 flex flex-wrap items-center justify-between gap-3 text-sm text-muted">
              <span>
                共 {total} 人，第 {page} / {pageCount} 页
              </span>
              <div className="flex items-center gap-2">
                <select
                  className="h-9 rounded-md border border-line bg-white px-2"
                  value={pageSize}
                  onChange={(event) => {
                    setPageSize(Number(event.target.value));
                    setPage(1);
                  }}
                >
                  <option value={20}>每页 20 人</option>
                  <option value={30}>每页 30 人</option>
                  <option value={50}>每页 50 人</option>
                </select>
                <Button
                  className="h-8 px-2.5 text-xs"
                  disabled={page <= 1}
                  onClick={() => setPage((current) => current - 1)}
                >
                  上一页
                </Button>
                <Button
                  className="h-8 px-2.5 text-xs"
                  disabled={page >= pageCount}
                  onClick={() =>
                    setPage((current) => Math.min(pageCount, current + 1))
                  }
                >
                  下一页
                </Button>
              </div>
            </div>
            {loading ? (
              <p className="mt-3 text-sm text-muted">正在加载候选人列表…</p>
            ) : null}
          </>
        )}
      </Section>
      <ResumeUploadDialog
        open={uploadOpen}
        onClose={() => setUploadOpen(false)}
        onUploaded={() => {
          setView("intakes");
          setIntakeRefreshVersion((current) => current + 1);
        }}
      />
      {timelineTarget ? (
        <Dialog
          title={timelineTarget.label}
          onClose={() => setTimelineTarget(undefined)}
        >
          <p className="mb-4 text-sm text-muted">
            {timelineTarget.candidateName} 的该后台任务执行步骤。
          </p>
          <WorkflowExecutionTimeline
            events={timelineEvents}
            loading={timelineLoading}
            error={timelineError}
          />
        </Dialog>
      ) : null}
      {assessmentResult ? (
        <AssessmentResultDialog
          state={assessmentResult}
          onClose={() => setAssessmentResult(undefined)}
          recoverySubmitting={assessmentRecoverySubmitting}
          onRecoveryAction={(action, input) => handleAssessmentRecoveryAction(action, input)}
          onOpenSourceRepair={(action) => openAssessmentSourceRepair(action, assessmentResult.item)}
        />
      ) : null}
      {resumeSourceTarget?.resumeSubmissionId ? (
        <ResumeSourcePreviewDialog
          submissionId={resumeSourceTarget.resumeSubmissionId}
          candidateName={resumeSourceTarget.candidateName}
          onClose={() => setResumeSourceTarget(undefined)}
          onEdit={() => {
            const params = new URLSearchParams(searchParams);
            params.set("view", "intakes");
            params.set("submissionId", resumeSourceTarget.resumeSubmissionId!);
            setResumeSourceTarget(undefined);
            setSearchParams(params, { replace: true });
          }}
        />
      ) : null}
      {hardPolicyTarget ? (
        <Dialog title="处理硬筛条件" onClose={() => setHardPolicyTarget(undefined)}>
          <p className="mb-4 text-sm leading-6 text-slate-700">
            请检查并保存该岗位的硬性筛选条件。保存后可重新运行硬筛，或关闭窗口后选择人工处理。
          </p>
          <HardScreeningPolicyPanel
            jobId={hardPolicyTarget.jobId}
            onSaved={(policy) => {
              const applicationId = hardPolicyTarget.applicationId;
              setHardPolicyTarget(undefined);
              if (policy.enabled) void handleRetryHardScreening(applicationId);
            }}
          />
        </Dialog>
      ) : null}
      {jobProfileTarget ? (
        <Dialog title="查看并处理岗位来源" onClose={() => setJobProfileTarget(undefined)}>
          <div className="space-y-3 text-sm leading-6 text-slate-700">
            <p>当前申请岗位：<span className="font-semibold text-ink">{jobProfileTarget.jobTitle}</span></p>
            {jobProfileTarget.jobMajorRequirement ? <p>专业要求：{jobProfileTarget.jobMajorRequirement}</p> : null}
            <p className="rounded-md bg-slate-50 p-3">系统无法使用本申请冻结的岗位画像。你可以重新处理本次岗位来源，或明确采用岗位当前要求后重新运行。</p>
          </div>
          <div className="mt-5 flex flex-wrap justify-end gap-2">
            <Button onClick={() => setJobProfileTarget(undefined)}>取消</Button>
            <Button
              disabled={jobProfileRepairIds.has(jobProfileTarget.applicationId)}
              onClick={() => {
                void handleRepairJobProfile(jobProfileTarget.applicationId, "reprocess_frozen");
                setJobProfileTarget(undefined);
              }}
            ><RefreshCw size={16} />重新处理本次岗位来源</Button>
            <Button
              variant="primary"
              disabled={jobProfileRepairIds.has(jobProfileTarget.applicationId)}
              onClick={() => {
                void handleRepairJobProfile(jobProfileTarget.applicationId, "adopt_current");
                setJobProfileTarget(undefined);
              }}
            ><RefreshCw size={16} />采用当前岗位要求</Button>
          </div>
        </Dialog>
      ) : null}
      {hardResult ? (
        <Dialog title="硬筛结果" onClose={() => setHardResult(undefined)}>
          <p className="text-sm text-slate-700">
            {hardResult.summary || "暂无汇总。"}
          </p>
          <div className="mt-3 space-y-2">
            {hardResult.ruleResults.map((rule) => (
              <div
                key={rule.rule_id}
                className="rounded-md border border-line p-3"
              >
                <div className="flex items-center justify-between gap-3">
                  <span className="font-semibold text-ink">{rule.name}</span>
                  <Badge
                    className={
                      rule.status === "passed"
                        ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                        : rule.status === "failed"
                          ? "border-rose-200 bg-rose-50 text-rose-700"
                          : "border-amber-200 bg-amber-50 text-amber-800"
                    }
                  >
                    {rule.status === "passed"
                      ? "通过"
                      : rule.status === "failed"
                        ? "未通过"
                        : "需人工复核"}
                  </Badge>
                </div>
                <dl className="mt-3 space-y-2 text-sm leading-6">
                  <div>
                    <dt className="inline font-medium text-slate-700">硬筛要求：</dt>
                    <dd className="inline text-slate-900">{rule.requirementText}</dd>
                  </div>
                  <div>
                    <dt className="inline font-medium text-slate-700">自动判断：</dt>
                    <dd className="inline text-slate-800">
                      {rule.status === "passed" ? "满足要求" : rule.status === "failed" ? "不满足要求" : "需要人工核对"}
                    </dd>
                  </div>
                  <div>
                    <dt className="inline font-medium text-slate-700">判断说明：</dt>
                    <dd className="inline text-slate-800">{rule.reason || "暂无自动判断说明。"}</dd>
                  </div>
                </dl>
                <div className="mt-3 rounded bg-slate-50 p-2 text-xs leading-5 text-muted">
                  <div className="font-medium text-slate-700">简历依据</div>
                  {rule.source_quotes.length > 0 ? (
                    <div className="mt-1 space-y-1">
                    {rule.source_quotes.map((quote, index) => (
                      <p key={index}>“{quote}”</p>
                    ))}
                    </div>
                  ) : (
                    <p className="mt-1">未找到可直接定位的简历原文，请人工核对候选人材料。</p>
                  )}
                </div>
              </div>
            ))}
          </div>
          {hardResultApplication &&
          (
            hasRecoveryAction(hardResultApplication.recoveryActions, "review_hard_screening_pass") ||
            hasRecoveryAction(hardResultApplication.recoveryActions, "review_hard_screening_reject")
          ) ? (
            <div className="mt-4 flex gap-2">
              {hasRecoveryAction(hardResultApplication.recoveryActions, "review_hard_screening_pass") ? (
                <Button
                  variant="primary"
                  onClick={() =>
                    void handleHardScreeningReview(hardResult.applicationId, "pass")
                  }
                >
                  人工通过
                </Button>
              ) : null}
              {hasRecoveryAction(hardResultApplication.recoveryActions, "review_hard_screening_reject") ? (
                <Button
                  variant="danger"
                  onClick={() =>
                    void handleHardScreeningReview(hardResult.applicationId, "reject")
                  }
                >
                  人工拒绝
                </Button>
              ) : null}
            </div>
          ) : null}
        </Dialog>
      ) : null}
      {reviewDialog ? (
        <Dialog
          title={
            reviewDialog.decision === "pass" ? "确认人工通过" : "确认人工拒绝"
          }
          onClose={() => setReviewDialog(undefined)}
        >
          {hardResult ? (
            <div className="mb-4 rounded-md border border-line bg-slate-50 p-3">
              <div className="text-sm font-semibold text-ink">本次确认的硬筛要求</div>
              <div className="mt-2 space-y-1.5 text-sm text-slate-700">
                {(hardResult.ruleResults.some((rule) => rule.status === "manual_review")
                  ? hardResult.ruleResults.filter((rule) => rule.status === "manual_review")
                  : hardResult.ruleResults
                ).map((rule) => (
                  <p key={rule.rule_id}><span className="font-medium">{rule.name}：</span>{rule.requirementText}</p>
                ))}
              </div>
            </div>
          ) : null}
          <label className="block text-sm font-medium text-slate-700">
            审核理由
            <textarea
              className="mt-2 min-h-28 w-full rounded-md border border-line p-3 text-sm"
              value={reviewReason}
              onChange={(event) => setReviewReason(event.target.value)}
              placeholder="请写明依据，便于后续审计。"
            />
          </label>
          <div className="mt-4 flex justify-end gap-2">
            <Button onClick={() => setReviewDialog(undefined)}>取消</Button>
            <Button
              variant={reviewDialog.decision === "pass" ? "primary" : "danger"}
              disabled={!reviewReason.trim()}
              onClick={() => void submitHardScreeningReview()}
            >
              确认提交
            </Button>
          </div>
        </Dialog>
      ) : null}
      {bulkDeleteOpen ? (
        <Dialog
          title={`删除 ${selectedApplicationIds.size} 份岗位申请`}
          onClose={() => !bulkDeleting && setBulkDeleteOpen(false)}
        >
          <p className="text-sm leading-6 text-slate-700">
            确认删除已选择的岗位申请吗？候选人档案及其其他岗位申请不受影响。
          </p>
          <div className="mt-3 rounded-md border border-line bg-slate-50 px-3 py-2 text-sm text-slate-700">
            {applications
              .filter((item) => selectedApplicationIds.has(item.applicationId))
              .slice(0, 3)
              .map((item) => (
                <div key={item.applicationId} className="truncate py-0.5">
                  {item.candidateName} · {item.jobTitle}
                </div>
              ))}
            {selectedApplicationIds.size > 3 ? (
              <div className="pt-0.5 text-xs text-muted">
                另有 {selectedApplicationIds.size - 3} 份申请
              </div>
            ) : null}
          </div>
          <p className="mt-3 rounded-md bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-800">
            删除后申请将从招聘流程和任务中心移除，已有简历、评分和面评记录不会立即物理清除。
          </p>
          {bulkDeleting ? (
            <p className="mt-3 text-sm text-muted" aria-live="polite">
              正在删除 {bulkDeleteProgress.completed}/{bulkDeleteProgress.total}
            </p>
          ) : null}
          <div className="mt-4 flex justify-end gap-2">
            <Button
              onClick={() => setBulkDeleteOpen(false)}
              disabled={bulkDeleting}
            >
              取消
            </Button>
            <Button
              variant="danger"
              onClick={() => void confirmBulkDeleteApplications()}
              disabled={bulkDeleting}
            >
              {bulkDeleting ? "正在删除" : `删除 ${selectedApplicationIds.size} 份申请`}
            </Button>
          </div>
        </Dialog>
      ) : null}
      {deleteTarget ? (
        <Dialog
          title="删除候选人申请"
          onClose={() => !deleting && setDeleteTarget(undefined)}
        >
          <p className="text-sm leading-6 text-slate-700">
            确认删除{" "}
            <span className="font-semibold text-ink">
              {deleteTarget.candidateName}
            </span>
            对“{deleteTarget.jobTitle}”岗位的申请？
          </p>
          <p className="mt-2 rounded-md bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-800">
            删除后，该申请将从候选人列表和任务中心移除；已有简历、评分和面评记录不会立即物理清除。
          </p>
          <div className="mt-4 flex justify-end gap-2">
            <Button
              disabled={deleting}
              onClick={() => setDeleteTarget(undefined)}
            >
              取消
            </Button>
            <Button
              variant="danger"
              disabled={deleting}
              onClick={() => void confirmDeleteApplication()}
            >
              {deleting ? "正在删除" : "删除申请"}
            </Button>
          </div>
        </Dialog>
      ) : null}
      {documentTarget ? (
        <ApplicationDocumentLibrary
          open
          applicationId={documentTarget.applicationId}
          candidateName={documentTarget.candidateName}
          mode="manage"
          onClose={() => setDocumentTarget(undefined)}
        />
      ) : null}
    </>
  );
}

function HardScreeningCellSummary({ item }: { item: ApplicationListItem }) {
  const preview = item.hardScreeningPreview ?? {
    totalCount: 0,
    passedCount: 0,
    failedCount: 0,
    reviewCount: 0,
    reasons: [],
  };
  const showsReasons =
    item.hardScreeningStatus === "failed" ||
    item.hardScreeningStatus === "manual_review";
  const visibleReasons = showsReasons ? preview.reasons.slice(0, 2) : [];
  const relevantCount =
    item.hardScreeningStatus === "failed"
      ? preview.failedCount
      : item.hardScreeningStatus === "manual_review"
        ? preview.reviewCount
        : 0;
  const remainingCount = Math.max(0, relevantCount - visibleReasons.length);
  const reasonTone =
    item.hardScreeningStatus === "failed" ? "text-rose-700" : "text-amber-800";

  return (
    <>
      <Badge className={hardScreeningTones[item.hardScreeningStatus]}>
        {hardScreeningLabels[item.hardScreeningStatus]}
      </Badge>
      {showsReasons && visibleReasons.length > 0 ? (
        <div
          className={`mt-1.5 max-w-64 space-y-1 text-xs leading-5 ${reasonTone}`}
        >
          {visibleReasons.map((reason, index) => (
            <p key={`${reason.ruleName}-${index}`}>
              <span className="font-medium">{reason.ruleName}：</span>
              {reason.reason}
            </p>
          ))}
          {remainingCount > 0 ? (
            <p className="font-medium">另有 {remainingCount} 项需要查看</p>
          ) : null}
        </div>
      ) : showsReasons && item.hardScreeningSummary ? (
        <p className={`mt-1.5 max-w-64 text-xs leading-5 ${reasonTone}`}>
          {item.hardScreeningSummary}
        </p>
      ) : item.hardScreeningStatus === "passed" && preview.totalCount > 0 ? (
        <p className="mt-1.5 text-xs leading-5 text-emerald-700">
          {preview.totalCount} 项硬性条件均通过
        </p>
      ) : null}
    </>
  );
}

function formatDateTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function formatExperience(value: string) {
  const text = value.trim();
  if (!text || ["待补充", "未提供", "未识别", "未知"].includes(text)) {
    return "";
  }
  if (/年|个月|以上|以下/.test(text)) return text;
  return /^\d+(?:\.\d+)?$/.test(text) ? `${text} 年经验` : text;
}

function AssessmentResultDialog({
  state,
  onClose,
  recoverySubmitting,
  onRecoveryAction,
  onOpenSourceRepair,
}: {
  state: AssessmentResultState;
  onClose: () => void;
  recoverySubmitting: boolean;
  onRecoveryAction: (action: string, input?: string) => Promise<void>;
  onOpenSourceRepair: (action: string) => void;
}) {
  const title = state.stage === "v1" ? "初步筛选结果" : state.stage === "v2" ? "一面后评估" : "二面后评估";
  const [editingFeedback, setEditingFeedback] = useState<string>();
  const [repairNotes, setRepairNotes] = useState("");
  if (state.loading) return <Dialog title={title} onClose={onClose}><p className="text-sm text-muted">正在加载评估结果…</p></Dialog>;
  if (state.error) return <Dialog title={title} onClose={onClose}><p className="text-sm text-rose-700">{state.error}</p></Dialog>;

  if (state.stage === "v1" && state.v1) {
    const summary = state.v1.screeningResult.summary;
    const snapshot: ScoreSnapshotView = summary ? {
      total: summary.overallScore ?? undefined,
      jobFit: summary.jobCapabilityFitScore ?? undefined,
      resumeExperience: summary.resumeExperienceScore ?? undefined,
      education: summary.educationBackgroundScore ?? undefined,
    } : {};
    return <Dialog title={title} onClose={onClose}>
      <ScoreResultSummary snapshot={snapshot} />
      <div className="mt-4 rounded-md border border-line p-3 text-sm">
        <div className="font-semibold text-ink">初步筛选结论</div>
        <p className="mt-1 leading-5 text-slate-700">{state.v1.decisionOverview.aiSummary.recommendationReason || "暂无结论。"}</p>
      </div>
    </Dialog>;
  }

  const view = state.stage === "v2" ? state.v2 : state.v3;
  if (!view) return <Dialog title={title} onClose={onClose}><p className="text-sm text-muted">暂无可展示的评估结果。</p></Dialog>;
  const current = readScoreSnapshot(state.stage === "v2" ? view.afterFirstScoreSnapshot : view.afterSecondScoreSnapshot) ?? {};
  const baseline = readScoreSnapshot(state.stage === "v2" ? view.screeningScoreSnapshot : view.afterFirstScoreSnapshot) ?? {};
  const changes = state.stage === "v2" ? view.afterFirstAssessmentChanges : view.afterSecondAssessmentChanges;
  const record = state.stage === "v2" ? view.firstOriginalRecord : view.secondOriginalRecord;
  const recoveryMessage = typeof view.workflowStatus.recoveryMessage === "string"
    ? view.workflowStatus.recoveryMessage
    : "本轮评估尚未生成正式结果，请先处理当前异常。";
  const recoveryActions = view.recoveryActions ?? [];
  return <Dialog title={title} onClose={onClose}>
    {recoveryActions.length > 0 ? (
      <div className="mb-4 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm leading-6 text-amber-900">
        <div className="font-semibold">{state.stage === "v2" ? "一面后评估" : "二面后评估"}待处理</div>
        <p>{recoveryMessage}</p>
      </div>
    ) : null}
    <ScoreResultSummary snapshot={current} baseline={baseline} changes={changes} />
    <InterviewRecordSummary record={record} label={state.stage === "v2" ? "一面记录" : "二面记录"} />
    <AssessmentSignals changes={changes} />
    {recoveryActions.length > 0 ? (
      <div className="mt-4 rounded-md border border-line p-3">
        <div className="text-sm font-semibold text-ink">可执行操作</div>
        <div className="mt-2 flex flex-wrap gap-2">
          {recoveryActions.map((action: RecoveryAction) => {
            const isEdit = action.action === "edit_first_interview_feedback" || action.action === "edit_second_interview_feedback";
            const isSourceNavigation = action.action === "repair_resume_source" || action.action === "repair_job_profile";
            return (
              <Button
                key={action.action}
                type="button"
                disabled={recoverySubmitting}
                onClick={() => {
                  if (isSourceNavigation) {
                    onOpenSourceRepair(action.action);
                    return;
                  }
                  if (isEdit) {
                    const original = state.stage === "v2" ? view.firstOriginalRecord : view.secondOriginalRecord;
                    setRepairNotes(original?.rawNotes?.content ?? "");
                    setEditingFeedback(action.action);
                    return;
                  }
                  void onRecoveryAction(action.action);
                }}
              >
                <RefreshCw size={16} />{action.label}
              </Button>
            );
          })}
        </div>
        {editingFeedback ? (
          <div className="mt-3 border-t border-line pt-3">
            <label className="text-sm font-medium text-ink">修正后的面评记录</label>
            <textarea
              className="mt-2 min-h-32 w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm"
              value={repairNotes}
              onChange={(event) => setRepairNotes(event.target.value)}
              placeholder="请输入本轮面评记录"
            />
            <div className="mt-2 flex justify-end gap-2">
              <Button type="button" disabled={recoverySubmitting} onClick={() => setEditingFeedback(undefined)}>取消</Button>
              <Button
                type="button"
                variant="primary"
                disabled={recoverySubmitting || !repairNotes.trim()}
                onClick={async () => {
                  if (!editingFeedback) return;
                  await onRecoveryAction(editingFeedback, repairNotes);
                  setEditingFeedback(undefined);
                }}
              >
                <RefreshCw size={16} />{recoverySubmitting ? "提交中" : "保存并重新计算"}
              </Button>
            </div>
          </div>
        ) : null}
      </div>
    ) : null}
  </Dialog>;
}

function ScoreResultSummary({ snapshot, baseline, changes }: { snapshot: ScoreSnapshotView; baseline?: ScoreSnapshotView; changes?: AssessmentChangeSet | null }) {
  const metrics = [
    ["总分", "total", snapshot.total, baseline?.total],
    ["岗位匹配", "job_fit", snapshot.jobFit, baseline?.jobFit],
    ["经历能力", "experience", snapshot.resumeExperience, baseline?.resumeExperience],
    ["学历背景", "education", snapshot.education, baseline?.education],
  ] as const;
  return <div className="overflow-hidden rounded-md border border-line">
    <div className="border-b border-line bg-slate-50 px-3 py-2 text-sm font-semibold text-ink">分数情况</div>
    <div className="divide-y divide-line">{metrics.map(([label, metric, value, previous]) => {
      const explicit = changes?.scoreChanges.find((item) => item.metric === metric);
      const delta = baseline ? explicit?.delta ?? (typeof value === "number" && typeof previous === "number" ? value - previous : 0) : undefined;
      return <div key={metric} className="flex items-center justify-between gap-3 px-3 py-2 text-sm">
        <span className="text-slate-700">{label}</span>
        <span className="font-semibold text-ink">{formatScore(value)}{baseline ? <span className={`ml-2 text-xs ${delta && delta > 0 ? "text-emerald-700" : delta && delta < 0 ? "text-rose-700" : "text-muted"}`}>({formatDelta(delta)})</span> : null}</span>
      </div>;
    })}</div>
  </div>;
}

function InterviewRecordSummary({ record, label }: { record?: { rawNotes?: { content: string }; recordedQuestions?: Array<{ questionText: string; answerSummary: string; interviewerNote: string }> }; label: string }) {
  const notes = record?.rawNotes?.content?.trim();
  const questions = (record?.recordedQuestions ?? []).filter((item) => item.answerSummary?.trim() || item.interviewerNote?.trim());
  if (!notes && questions.length === 0) return <div className="mt-4 rounded-md border border-line p-3 text-sm text-muted">{label}暂无记录。</div>;
  return <div className="mt-4 rounded-md border border-line p-3">
    <div className="text-sm font-semibold text-ink">{label}</div>
    {notes ? <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-slate-700">{notes}</p> : null}
    {questions.length > 0 ? <div className="mt-2 space-y-2">{questions.map((item, index) => <div key={index} className="rounded bg-slate-50 p-2 text-xs leading-5 text-slate-700"><div className="font-medium">{item.questionText}</div><div>{item.answerSummary}</div>{item.interviewerNote ? <div className="text-muted">备注：{item.interviewerNote}</div> : null}</div>)}</div> : null}
  </div>;
}

function AssessmentSignals({ changes }: { changes?: AssessmentChangeSet | null }) {
  if (!changes) return null;
  const signals = [
    ...changes.strengths.map((item) => ({ ...item, kind: "优势" })),
    ...changes.weaknesses.map((item) => ({ ...item, kind: "待确认" })),
  ];
  return <div className="mt-4 space-y-3">
    {signals.length > 0 ? <div><div className="text-sm font-semibold text-ink">本轮表现变化</div><div className="mt-2 space-y-2">{signals.map((item) => <div key={`${item.kind}-${item.signalKey}`} className="rounded-md border border-line p-3 text-sm"><div className="font-medium text-slate-800">{item.kind}：{item.title}</div><p className="mt-1 leading-5 text-slate-700">{item.summary || "暂无说明。"}</p></div>)}</div></div> : null}
    {changes.verificationFocus.length > 0 ? <div><div className="text-sm font-semibold text-ink">面试关注事项</div><div className="mt-2 space-y-2">{changes.verificationFocus.map((item) => <div key={item.targetId} className="rounded-md border border-line p-3 text-sm"><div className="font-medium text-slate-800">{item.title}</div><p className="mt-1 leading-5 text-slate-700">{item.goal || item.reason || "暂无说明。"}</p></div>)}</div></div> : null}
  </div>;
}

function formatScore(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(1) : "待核验";
}

function formatDelta(value: number | null | undefined): string {
  const delta = typeof value === "number" && Number.isFinite(value) ? value : 0;
  return `${delta >= 0 ? "+" : ""}${delta.toFixed(1)}`;
}

function FilterChip({ label, onRemove }: { label: string; onRemove: () => void }) {
  return (
    <span className="inline-flex h-7 items-center gap-1.5 rounded-md border border-blue-200 bg-blue-50 pl-2.5 pr-1.5 text-xs font-medium text-blue-700">
      {label}
      <button
        type="button"
        className="grid size-5 place-items-center rounded text-blue-500 hover:bg-blue-100 hover:text-blue-800"
        aria-label={`移除筛选：${label}`}
        onClick={onRemove}
      >
        <X size={12} />
      </button>
    </span>
  );
}

function FilterField({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid gap-1.5 text-xs font-medium text-slate-600">
      <span>{label}</span>
      {children}
    </div>
  );
}

function Dialog({
  title,
  children,
  onClose,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
}) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <div className="max-h-[85vh] w-full max-w-2xl overflow-auto rounded-lg bg-white p-5 shadow-xl">
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-lg font-semibold text-ink">{title}</h2>
          <button
            type="button"
            className="text-sm text-muted hover:text-ink"
            onClick={onClose}
          >
            关闭
          </button>
        </div>
        <div className="mt-4">{children}</div>
      </div>
    </div>
  );
}
