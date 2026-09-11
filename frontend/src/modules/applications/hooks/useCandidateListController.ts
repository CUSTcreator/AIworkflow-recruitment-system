import { useCallback, useEffect, useRef, useState } from "react";
import {
  deleteApplication,
  getApplicationFilterOptions,
  getApplicationList,
  getHardScreeningResult,
  retryHardScreening,
  retryInitialAssessment,
  reviewHardScreening,
  type ApplicationListItem,
  type ApplicationFilterDepartmentOption,
  type ApplicationFilterJobOption,
  type HardScreeningFilter,
  type HardScreeningResult
} from "@/modules/applications/api";
import { useWorkflowActions } from "@/modules/recruitment_workflow/workflowActions";
import type { JobProfileRecoveryMode } from "@/modules/applications/commands";
import { useAuth } from "@/modules/auth/AuthProvider";
import { hasBusinessPermission } from "@/modules/auth/permissions";
import { getImportRecords } from "@/modules/jobs/api";
import {
  announceNavigationNotificationChange,
  markNavigationNotificationRead
} from "@/modules/tasks/notificationsApi";
import { usePeriodicRefresh } from "@/shared/hooks/usePeriodicRefresh";
import { isWorkflowProcessActive } from "@/shared/workflows/process";
import { useToast } from "@/shared/toast/ToastProvider";

const BULK_DELETE_CONCURRENCY = 3;

async function runWithConcurrency<T>(
  values: T[],
  limit: number,
  task: (value: T) => Promise<void>,
): Promise<void> {
  let nextIndex = 0;
  async function runner() {
    while (nextIndex < values.length) {
      const value = values[nextIndex];
      nextIndex += 1;
      await task(value);
    }
  }
  await Promise.all(
    Array.from({ length: Math.min(limit, values.length) }, () => runner()),
  );
}

export type CandidateStageFilter = "in_progress" | "passed" | "rejected" | "cancelled";
export type HardScreeningListFilter = "all" | HardScreeningFilter;
export type SubmittedDateRange = "all" | "today" | "3d" | "7d" | "30d" | "this_month" | "90d" | "custom";
export type ApplicationListSort = "submitted_desc" | "submitted_asc" | "score_desc" | "score_asc";

function localDateStart(value: string): Date | undefined {
  const [year, month, day] = value.split("-").map(Number);
  if (!year || !month || !day) return undefined;
  return new Date(year, month - 1, day);
}

function submittedDateBounds(
  range: SubmittedDateRange,
  customFrom: string,
  customTo: string
): { submittedFrom?: string; submittedTo?: string } {
  const now = new Date();
  if (range === "all") return {};
  if (range === "today") {
    return { submittedFrom: new Date(now.getFullYear(), now.getMonth(), now.getDate()).toISOString() };
  }
  if (range === "this_month") {
    return { submittedFrom: new Date(now.getFullYear(), now.getMonth(), 1).toISOString() };
  }
  if (range === "custom") {
    const from = localDateStart(customFrom);
    const to = localDateStart(customTo);
    if (to) to.setDate(to.getDate() + 1);
    return { submittedFrom: from?.toISOString(), submittedTo: to?.toISOString() };
  }
  const days = Number(range.slice(0, -1));
  return { submittedFrom: new Date(Date.now() - days * 24 * 60 * 60 * 1000).toISOString() };
}

export function useCandidateListController(enabled = true) {
  const { repairJobProfile, runScoring, retryPostFirstScoring, retryPostSecondScoring } = useWorkflowActions();
  const { token, user } = useAuth();
  const toast = useToast();
  const [applications, setApplications] = useState<ApplicationListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [stageCounts, setStageCounts] = useState<Record<CandidateStageFilter, number>>({ in_progress: 0, passed: 0, rejected: 0, cancelled: 0 });
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState("");
  const [keyword, setKeyword] = useState("");
  const [debouncedKeyword, setDebouncedKeyword] = useState("");
  const [status, setStatus] = useState<CandidateStageFilter>("in_progress");
  const [departments, setDepartments] = useState<ApplicationFilterDepartmentOption[]>([]);
  const [jobs, setJobs] = useState<ApplicationFilterJobOption[]>([]);
  const [departmentId, setDepartmentId] = useState("");
  const [jobId, setJobId] = useState("");
  const [applicationStatus, setApplicationStatus] = useState("");
  const [hardScreeningFilter, setHardScreeningFilter] = useState<HardScreeningListFilter>("all");
  const [highestDegree, setHighestDegree] = useState("");
  const [majorKeyword, setMajorKeyword] = useState("");
  const [minimumScore, setMinimumScore] = useState("");
  const [overdueOnly, setOverdueOnly] = useState(false);
  const [attentionOnly, setAttentionOnly] = useState(false);
  const [dateRange, setDateRange] = useState<SubmittedDateRange>("all");
  const [customDateFrom, setCustomDateFrom] = useState("");
  const [customDateTo, setCustomDateTo] = useState("");
  const [sort, setSort] = useState<ApplicationListSort>("score_desc");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(30);
  const [scoringIds, setScoringIds] = useState<Set<string>>(new Set());
  const [assessmentRetryIds, setAssessmentRetryIds] = useState<Set<string>>(new Set());
  const [hardScreeningRetryIds, setHardScreeningRetryIds] = useState<Set<string>>(new Set());
  const [initialAssessmentRetryIds, setInitialAssessmentRetryIds] = useState<Set<string>>(new Set());
  const [jobProfileRepairIds, setJobProfileRepairIds] = useState<Set<string>>(new Set());
  const [hardResult, setHardResult] = useState<HardScreeningResult>();
  const [reviewDialog, setReviewDialog] = useState<{ applicationId: string; decision: "pass" | "reject" }>();
  const [reviewReason, setReviewReason] = useState("");
  const [uploadOpen, setUploadOpen] = useState(false);
  const [actionMenu, setActionMenu] = useState<{ item: ApplicationListItem; top: number; left: number; placement: "above" | "below"; maxHeight: number }>();
  const [deleteTarget, setDeleteTarget] = useState<ApplicationListItem>();
  const [documentTarget, setDocumentTarget] = useState<ApplicationListItem>();
  const [deleting, setDeleting] = useState(false);
  const [bulkDeleting, setBulkDeleting] = useState(false);
  const candidateReadMarked = useRef(false);
  const actionMenuRef = useRef<HTMLDivElement>(null);
  const actionMenuItemRef = useRef<HTMLButtonElement>(null);

  const loadApplications = useCallback(async (silent = false) => {
    if (!token) return;
    if (!silent) setLoading(true);
    setListError("");
    try {
      if (dateRange === "custom" && customDateFrom && customDateTo && customDateFrom > customDateTo) {
        setListError("自定义时间的开始日期不能晚于结束日期");
        return;
      }
      const { submittedFrom, submittedTo } = submittedDateBounds(dateRange, customDateFrom, customDateTo);
      const scoreSort = sort.startsWith("score");
      const view = await getApplicationList(token, {
        page,
        pageSize,
        status: applicationStatus || undefined,
        group: status,
        departmentId: departmentId || undefined,
        jobId: jobId || undefined,
        hardScreeningStatus: hardScreeningFilter === "all" ? undefined : hardScreeningFilter,
        highestDegree: highestDegree || undefined,
        majorKeyword: majorKeyword || undefined,
        minimumScore: minimumScore === "" ? undefined : Number(minimumScore),
        overdueOnly,
        attentionOnly,
        keyword: debouncedKeyword,
        submittedFrom,
        submittedTo,
        sortBy: scoreSort ? "currentScore" : "submittedAt",
        sortOrder: sort.endsWith("asc") ? "asc" : "desc"
      });
      setApplications(view.items);
      setTotal(view.total);
      setStageCounts(view.groupCounts);
      if (!silent && !candidateReadMarked.current) {
        candidateReadMarked.current = true;
        void markNavigationNotificationRead(token, "candidate_application")
          .then(announceNavigationNotificationChange)
          .catch(() => { candidateReadMarked.current = false; });
      }
    } catch (error) {
      setListError(error instanceof Error ? error.message : "候选人列表加载失败");
    } finally {
      if (!silent) setLoading(false);
    }
  }, [applicationStatus, attentionOnly, customDateFrom, customDateTo, dateRange, debouncedKeyword, departmentId, hardScreeningFilter, highestDegree, jobId, majorKeyword, minimumScore, overdueOnly, page, pageSize, sort, status, token]);

  useEffect(() => {
    if (enabled) void loadApplications();
  }, [enabled, loadApplications]);

  useEffect(() => {
    if (!actionMenu) return;
    actionMenuRef.current
      ?.querySelector<HTMLElement>('[role="menuitem"]')
      ?.focus({ preventScroll: true });
    const closeMenu = () => setActionMenu(undefined);
    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (actionMenuRef.current?.contains(target) || (event.target as Element).closest?.("[data-candidate-action-trigger]")) return;
      closeMenu();
    };
    const handleKeyDown = (event: KeyboardEvent) => { if (event.key === "Escape") closeMenu(); };
    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    window.addEventListener("resize", closeMenu);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
      window.removeEventListener("resize", closeMenu);
    };
  }, [actionMenu]);

  useEffect(() => {
    if (!token || !enabled) return;
    void getApplicationFilterOptions(token)
      .then((options) => {
        setDepartments(options.departments ?? []);
        setJobs(options.jobs ?? []);
      })
      .catch(() => {
        setDepartments([]);
        setJobs([]);
      });
  }, [enabled, token]);



  useEffect(() => {
    const timer = window.setTimeout(() => {
      setDebouncedKeyword(keyword.trim());
      setPage(1);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [keyword]);

  // 简历重建也属于异步工作；只要任一岗位申请仍在重建，就持续刷新招聘流程列表。
  const hasActiveWork = applications.some((item) => (
    isWorkflowProcessActive(item.screeningProcess)
    || isWorkflowProcessActive(item.hardScreeningProcess)
    || isWorkflowProcessActive(item.assessmentUpdateProcess)
    || isWorkflowProcessActive(item.resumeRebuildProcess)
    // 重建任务刚入队时检查点可能尚未写入；不能因此停止轮询。
    || item.resumeRebuildStatus === "processing"
    // 岗位画像未完成时，Application 仍留在招聘流程列表中，必须自动刷新。
    || ["job_profile_queued", "job_profile_processing", "hard_screening_queued", "hard_screening_running", "v1_queued", "v1_running"]
      .includes(item.preScreeningProcess?.status ?? "")
  ));
  usePeriodicRefresh(() => loadApplications(true), enabled && hasActiveWork);

  const handleRetryInitialAssessment = useCallback(async (applicationId: string) => {
    if (!token) return;
    setInitialAssessmentRetryIds((current) => new Set(current).add(applicationId));
    try {
      await retryInitialAssessment(token, applicationId);
      toast.success("初步筛选任务已重新启动");
      await loadApplications();
    } finally {
      setInitialAssessmentRetryIds((current) => {
        const next = new Set(current);
        next.delete(applicationId);
        return next;
      });
    }
  }, [loadApplications, toast, token]);

  const handleRunScoring = useCallback(async (applicationId: string) => {
    setScoringIds((current) => new Set(current).add(applicationId));
    try {
      await runScoring(applicationId);
      await loadApplications();
    } finally {
      setScoringIds((current) => {
        const next = new Set(current);
        next.delete(applicationId);
        return next;
      });
    }
  }, [loadApplications, runScoring]);

  const handleRepairJobProfile = useCallback(async (
    applicationId: string,
    mode: JobProfileRecoveryMode = "reprocess_frozen",
  ) => {
    setJobProfileRepairIds((current) => new Set(current).add(applicationId));
    try {
      await repairJobProfile(applicationId, mode);
      await loadApplications();
    } finally {
      setJobProfileRepairIds((current) => {
        const next = new Set(current);
        next.delete(applicationId);
        return next;
      });
    }
  }, [loadApplications, repairJobProfile]);

  const handleRetryAssessmentUpdate = useCallback(async (
    applicationId: string,
    stage: "first" | "second",
  ) => {
    setAssessmentRetryIds((current) => new Set(current).add(applicationId));
    try {
      if (stage === "first") await retryPostFirstScoring(applicationId);
      else await retryPostSecondScoring(applicationId);
      await loadApplications();
    } finally {
      setAssessmentRetryIds((current) => {
        const next = new Set(current);
        next.delete(applicationId);
        return next;
      });
    }
  }, [loadApplications, retryPostFirstScoring, retryPostSecondScoring]);
  const handleHardScreeningReview = useCallback((applicationId: string, decision: "pass" | "reject") => {
    setReviewDialog({ applicationId, decision });
    setReviewReason("");
  }, []);

  const handleRetryHardScreening = useCallback(async (applicationId: string) => {
    if (!token) return;
    setHardScreeningRetryIds((current) => new Set(current).add(applicationId));
    try {
      await retryHardScreening(token, applicationId);
      toast.success("硬筛重试任务已提交");
      await loadApplications();
    } finally {
      setHardScreeningRetryIds((current) => {
        const next = new Set(current);
        next.delete(applicationId);
        return next;
      });
    }
  }, [loadApplications, toast, token]);

  const submitHardScreeningReview = useCallback(async () => {
    if (!token || !reviewDialog || !reviewReason.trim()) return;
    await reviewHardScreening(token, reviewDialog.applicationId, reviewDialog.decision, reviewReason.trim());
    setReviewDialog(undefined);
    setReviewReason("");
    setHardResult(undefined);
    await loadApplications();
  }, [loadApplications, reviewDialog, reviewReason, token]);

  const showHardScreeningResult = useCallback(async (applicationId: string) => {
    if (!token) return;
    setHardResult(await getHardScreeningResult(token, applicationId));
  }, [token]);

  const confirmDeleteApplication = useCallback(async () => {
    if (!token || !deleteTarget || deleting) return;
    setDeleting(true);
    setListError("");
    try {
      await deleteApplication(token, deleteTarget.applicationId);
      setDeleteTarget(undefined);
      await loadApplications();
    } catch (error) {
      setListError(error instanceof Error ? error.message : "删除申请失败");
    } finally {
      setDeleting(false);
    }
  }, [deleteTarget, deleting, loadApplications, token]);

  const deleteApplications = useCallback(async (
    applicationIds: string[],
    onProgress?: (completed: number, total: number) => void,
  ) => {
    if (!token || bulkDeleting || applicationIds.length === 0) {
      return { successIds: [] as string[], failedIds: applicationIds };
    }
    setBulkDeleting(true);
    const successIds: string[] = [];
    const failedIds: string[] = [];
    try {
      let completed = 0;
      await runWithConcurrency(applicationIds, BULK_DELETE_CONCURRENCY, async (applicationId) => {
        try {
          await deleteApplication(token, applicationId);
          successIds.push(applicationId);
        } catch {
          failedIds.push(applicationId);
        } finally {
          completed += 1;
          onProgress?.(completed, applicationIds.length);
        }
      });
      if (successIds.length > 0) await loadApplications();
      if (failedIds.length > 0) {
        toast.warning(
          successIds.length > 0
            ? `已删除${successIds.length}份申请，${failedIds.length}份删除失败`
            : `${failedIds.length}份申请删除失败`,
        );
      } else {
        toast.success(`已删除${successIds.length}份申请`);
      }
      return { successIds, failedIds };
    } finally {
      setBulkDeleting(false);
    }
  }, [bulkDeleting, loadApplications, toast, token]);

  return {
    token, user, applications, total, stageCounts, loading, listError,
    keyword, setKeyword, status, setStatus, departments, jobs,
    departmentId, setDepartmentId, jobId, setJobId,
    applicationStatus, setApplicationStatus,
    hardScreeningFilter, setHardScreeningFilter, dateRange, setDateRange,
    highestDegree, setHighestDegree, majorKeyword, setMajorKeyword,
    minimumScore, setMinimumScore, overdueOnly, setOverdueOnly,
    attentionOnly, setAttentionOnly,
    customDateFrom, setCustomDateFrom, customDateTo, setCustomDateTo,
    sort, setSort, page, setPage, pageSize, setPageSize, scoringIds, assessmentRetryIds, hardScreeningRetryIds, initialAssessmentRetryIds, jobProfileRepairIds,
    hardResult, setHardResult, reviewDialog, setReviewDialog,
    reviewReason, setReviewReason, uploadOpen, setUploadOpen,
    actionMenu, setActionMenu, actionMenuRef, actionMenuItemRef, deleteTarget,
    setDeleteTarget, documentTarget, setDocumentTarget, deleting, bulkDeleting, loadApplications,
    handleRunScoring, handleRepairJobProfile, handleRetryInitialAssessment, handleRetryAssessmentUpdate, handleRetryHardScreening, handleHardScreeningReview, submitHardScreeningReview,
    showHardScreeningResult, confirmDeleteApplication, deleteApplications,
    pageCount: Math.max(1, Math.ceil(total / pageSize))
  };
}
