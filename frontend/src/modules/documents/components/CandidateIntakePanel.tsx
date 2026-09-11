import {
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Ellipsis,
  Eye,
  FileClock,
  FileText,
  RefreshCw,
  Trash2,
  Upload,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link, useLocation, useSearchParams } from "react-router-dom";

import { useAuth } from "@/modules/auth/AuthProvider";
import { workspaceEntryState } from "@/shared/navigation/workspaceReturn";
import {
  getHardScreeningResult,
  type HardScreeningResult,
} from "@/modules/applications/api";
import type { ApplicationStatus } from "@/modules/applications/contracts";
import { statusLabels, statusTone } from "@/modules/applications/status";
import { AuthenticatedPdfLink } from "@/modules/documents/components/AuthenticatedPdfLink";
import { CandidateDocumentLibrary } from "@/modules/documents/components/CandidateDocumentLibrary";
import {
  createApplicationsFromIntake,
  deleteCandidate,
  decideResumeDuplicate,
  getAmbiguousDuplicateCandidates,
  getCandidateIntake,
  getCandidateIntakeWorkflowTimeline,
  getCandidateIntakeList,
  getManualRoutingOptions,
  getResumeCorrectionDraft,
  markCandidateIntakesRead,
  rebuildCandidateFromResume,
  retryCandidateResumePublish,
  retryCandidateRouting,
  replaceCandidateResume,
  resolveAmbiguousDuplicate,
  submitResumeManualCorrection,
  type CandidateIntake,
  type CandidateIntakeListItem,
  type CandidateIntakeStatus,
  type DuplicateCandidateOption,
  type ManualRoutingOptions,
  type ResumeCorrectionDraft,
  type ResumeCorrectionContextItem,
  type ResumeCorrectionSourceBlock,
  type ResumeCorrectionSourceBullet,
} from "@/modules/documents/resumeApi";
import {
  hasRecoveryAction,
  recoveryAction,
} from "@/shared/recovery/actions";
import { Badge } from "@/shared/ui/Badge";
import { Button } from "@/shared/ui/Button";
import { usePeriodicRefresh } from "@/shared/hooks/usePeriodicRefresh";
import { useToast } from "@/shared/toast/ToastProvider";
import {
  isWorkflowProcessActive,
  type WorkflowExecutionEvent,
  type WorkflowProcessStatus,
} from "@/shared/workflows/process";
import { formatWorkflowTime } from "@/shared/workflows/time";
import { WorkflowExecutionTimeline } from "@/shared/workflows/WorkflowExecutionTimeline";

const filters: Array<{ value: CandidateIntakeStatus; label: string }> = [
  { value: "all", label: "全部" },
  { value: "processing", label: "处理中" },
  { value: "review_required", label: "需确认" },
  { value: "failed", label: "处理失败" },
  { value: "completed", label: "已完成" },
];

function tone(
  status: CandidateIntakeListItem["bucket"],
): "blue" | "amber" | "rose" | "green" {
  if (status === "completed") return "green";
  if (status === "failed") return "rose";
  if (status === "review_required") return "amber";
  return "blue";
}

function label(status: CandidateIntakeListItem["bucket"]): string {
  return {
    processing: "处理中",
    review_required: "需确认",
    failed: "处理失败",
    completed: "已完成",
  }[status];
}

function formatTime(value: string): string {
  return formatWorkflowTime(value, {
    hour12: false,
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function applicationStatusView(status: string): {
  label: string;
  tone: string;
} {
  const knownStatus = status as ApplicationStatus;
  return {
    label: statusLabels[knownStatus] ?? "招聘流程状态更新中",
    tone:
      statusTone[knownStatus] ?? "border-slate-200 bg-slate-50 text-slate-600",
  };
}

function isDuplicateResumeReview(item: CandidateIntakeListItem): boolean {
  return [
    "duplicate",
    "duplicate_match",
    "duplicate_ambiguous",
    "duplicate_blocked",
  ].includes(String(item.reviewKind ?? ""));
}

type CorrectionProject = {
  // 仅用于把校正结果关联回已有结构化项目；这是后端内部键，不得渲染为用户可编辑字段。
  experienceUnitId: string;
  title: string;
  titleBlockIds: string[];
  contextBlockIds: string[];
  workEvidenceBlockIds: string[];
  contextItems: ResumeCorrectionContextItem[];
  sourceBullets: ResumeCorrectionSourceBullet[];
};

type CorrectionEducation = {
  school: string;
  degreeLevel: string;
  status: string;
  major: string;
  startYear: string;
  graduationYear: string;
  sourceBlockIds: string[];
};

type CorrectionFact = {
  value: string;
  sourceBlockIds: string[];
};

type CorrectionSkill = {
  name: string;
  details: string;
  sourceBlockIds: string[];
};

function SourceBlockPicker({
  blocks,
  selected,
  onChange,
  title,
}: {
  blocks: ResumeCorrectionSourceBlock[];
  selected: string[];
  onChange: (next: string[]) => void;
  title: string;
}) {
  const selectedCount = blocks.filter((block) =>
    selected.includes(block.blockId),
  ).length;

  return (
    <details className="mt-2 rounded-md border border-slate-200 bg-slate-50">
      {/*
       * 原文选择只是结构化字段的来源校正工具，默认折叠，避免完整解析块
       * 盖过用户真正需要审核的结构化结果。
       */}
      <summary className="flex cursor-pointer items-center justify-between gap-3 px-3 py-2 text-xs font-medium text-slate-600">
        <span>调整关联原文：{title}</span>
        <span className={selectedCount ? "text-emerald-700" : "text-amber-700"}>
          {selectedCount ? `已关联 ${selectedCount} 段` : "待选择"}
        </span>
      </summary>
      <div className="max-h-48 space-y-1 overflow-auto border-t border-slate-200 p-2">
        {blocks.map((block) => {
          const checked = selected.includes(block.blockId);
          return (
            <label
              key={block.blockId}
              className={`flex cursor-pointer items-start gap-2 rounded px-2 py-1.5 text-xs ${checked ? "bg-blue-50 text-blue-900" : "text-slate-600 hover:bg-white"}`}
            >
              <input
                className="mt-0.5"
                type="checkbox"
                checked={checked}
                onChange={() =>
                  onChange(
                    checked
                      ? selected.filter((value) => value !== block.blockId)
                      : [...selected, block.blockId],
                  )
                }
              />
              <span className="whitespace-pre-wrap leading-5">
                {block.text}
              </span>
            </label>
          );
        })}
        {!blocks.length ? (
          <p className="px-2 py-1 text-xs text-muted">没有可用原文片段</p>
        ) : null}
      </div>
    </details>
  );
}

const contextTypeLabels: Record<string, string> = {
  project_date: "时间",
  project_description: "项目介绍",
  tech_stack: "技术栈",
  development_environment: "开发环境",
  other_context: "其他信息",
};

function StructuredProjectPreview({ project }: { project: CorrectionProject }) {
  const contexts = project.contextItems.filter((item) => item.text.trim());
  const bullets = project.sourceBullets.filter((item) => item.text.trim());

  return (
    <div
      className="mt-3 rounded-md border border-slate-200 bg-slate-50 p-3"
      data-testid="structured-project-preview"
    >
      <div className="mb-2 text-xs font-medium text-slate-600">
        当前识别到的项目内容
      </div>
      {/*
       * 这里只展示结构化阶段的可读文本。WorkUnit 是评分内部的派生分组，
       * 不在用户校正页面出现；来源引用只通过“已关联原文”表达，避免把内部
       * block_id、bullet_id 等实现标识暴露给业务用户。
       */}
      {contexts.length ? (
        <div className="space-y-2">
          {contexts.map((item) => (
            <div key={item.contextId || `${item.contextType}-${item.text}`} className="text-sm text-slate-700">
              <span className="mr-2 inline-flex rounded bg-white px-1.5 py-0.5 text-[11px] font-medium text-slate-500">
                {contextTypeLabels[item.contextType] ?? "项目上下文"}
              </span>
              <span className="whitespace-pre-wrap leading-6">{item.text}</span>
              {item.sourceRefs.length ? (
                <span className="ml-2 text-[11px] text-slate-400">已关联原文</span>
              ) : null}
            </div>
          ))}
        </div>
      ) : null}
      {bullets.length ? (
        <div className={`${contexts.length ? "mt-3 border-t border-slate-200 pt-3" : ""} space-y-2`}>
          <div className="text-xs font-medium text-slate-500">职责和成果</div>
          {bullets.map((item) => (
            <div key={item.sourceBulletId || item.text} className="text-sm text-slate-700">
              <span className="mr-2 text-slate-400">·</span>
              <span className="whitespace-pre-wrap leading-6">{item.text}</span>
              {item.sourceRefs.length ? (
                <span className="ml-2 text-[11px] text-slate-400">已关联原文</span>
              ) : null}
            </div>
          ))}
        </div>
      ) : null}
      {!contexts.length && !bullets.length ? (
        <p className="text-sm text-muted">暂无可展示的项目内容</p>
      ) : null}
    </div>
  );
}

function CandidateIntakeStageCell({ item }: { item: CandidateIntakeListItem }) {
  // 业务阶段与 WorkflowProcess 都会包含“处理中”等状态。表格只保留一个主状态，
  // 运行中的工作流仅补充“正在执行哪一步”，避免同一状态上下重复显示。
  const process = item.process;
  const processOverrides: Partial<
    Record<WorkflowProcessStatus, { label: string; tone: string }>
  > = {
    retry_wait: {
      label: "自动重试中",
      tone: "border-amber-200 bg-amber-50 text-amber-800",
    },
    waiting_external: {
      label: "等待外部结果",
      tone: "border-amber-200 bg-amber-50 text-amber-800",
    },
    blocked: {
      label: "待确认",
      tone: "border-amber-200 bg-amber-50 text-amber-800",
    },
    failed: {
      label: "处理失败",
      tone: "border-rose-200 bg-rose-50 text-rose-700",
    },
    cancelled: {
      label: "已取消",
      tone: "border-slate-200 bg-slate-50 text-slate-600",
    },
  };
  const processOverride = process
    ? processOverrides[process.processStatus]
    : undefined;
  const fallbackTone =
    item.bucket === "completed"
      ? "border-emerald-200 bg-emerald-50 text-emerald-700"
      : item.bucket === "failed"
        ? "border-rose-200 bg-rose-50 text-rose-700"
        : item.bucket === "review_required"
          ? "border-amber-200 bg-amber-50 text-amber-800"
          : "border-blue-200 bg-blue-50 text-blue-700";
  const primaryLabel =
    (processOverride?.label ?? item.stage) || label(item.bucket);
  const detail =
    isWorkflowProcessActive(process) &&
    process?.currentStepLabel !== primaryLabel
      ? process?.currentStepLabel
      : "";
  const message =
    process?.publicMessage &&
    process.publicMessage !== primaryLabel &&
    process.publicMessage !== detail
      ? process.publicMessage
      : item.reviewReason && item.reviewReason !== primaryLabel
        ? item.reviewReason
        : "";

  return (
    <div className="space-y-1">
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge className={processOverride?.tone ?? fallbackTone}>
          {primaryLabel}
        </Badge>
        {detail ? (
          <span className="text-xs text-slate-600">· {detail}</span>
        ) : null}
        {item.processingQuality.status === "degraded" ? (
          <span
            className="text-xs text-muted"
            title={item.processingQuality.message || "部分结果按保守规则处理"}
          >
            · 部分结果按保守规则处理
          </span>
        ) : null}
        {item.routingStatus === "manual_selection_available" ? (
          <Badge className="border-amber-200 bg-amber-50 text-amber-800">
            待选择投递岗位
          </Badge>
        ) : null}
      </div>
      {message ? (
        <p className="max-w-64 text-xs leading-5 text-muted">{message}</p>
      ) : null}
      {item.routingStatus === "manual_selection_available" &&
      item.routingReason ? (
        <p className="max-w-64 text-xs leading-5 text-amber-800">
          {item.routingReason}
        </p>
      ) : null}
      {process?.nextAttemptAt ? (
        <p className="text-xs text-muted">
          下次尝试：{formatTime(process.nextAttemptAt)}
        </p>
      ) : null}
    </div>
  );
}

export function CandidateIntakePanel({
  onUnreadCountChange,
  onRequestUpload,
  refreshVersion = 0,
}: {
  onUnreadCountChange?: (count: number) => void;
  onRequestUpload?: () => void;
  refreshVersion?: number;
}) {
  const location = useLocation();
  const [searchParams] = useSearchParams();
  const targetSubmissionId = searchParams.get("submissionId") ?? "";
  const { token, user } = useAuth();
  const toast = useToast();
  const [status, setStatus] = useState<CandidateIntakeStatus>("all");
  const [keyword, setKeyword] = useState("");
  const [page, setPage] = useState(1);
  const [items, setItems] = useState<CandidateIntakeListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [workingId, setWorkingId] = useState("");
  const replacementInputRef = useRef<HTMLInputElement>(null);
  const [replacementTarget, setReplacementTarget] =
    useState<CandidateIntakeListItem>();
  const [applicationsTarget, setApplicationsTarget] =
    useState<CandidateIntakeListItem>();
  const [hardScreeningTarget, setHardScreeningTarget] = useState<
    CandidateIntakeListItem["applications"][number]
  >();
  const [hardScreeningResult, setHardScreeningResult] =
    useState<HardScreeningResult>();
  const [hardScreeningLoading, setHardScreeningLoading] = useState(false);
  const [hardScreeningError, setHardScreeningError] = useState("");
  const [candidateDocumentTarget, setCandidateDocumentTarget] =
    useState<CandidateIntakeListItem>();
  const [correctionTarget, setCorrectionTarget] =
    useState<CandidateIntakeListItem>();
  const [correctionDraft, setCorrectionDraft] =
    useState<ResumeCorrectionDraft>();
  const [correctionLoading, setCorrectionLoading] = useState(false);
  const [correctionEducations, setCorrectionEducations] = useState<
    CorrectionEducation[]
  >([]);
  const [correctionExperienceYears, setCorrectionExperienceYears] =
    useState<CorrectionFact>({ value: "", sourceBlockIds: [] });
  const [correctionProjects, setCorrectionProjects] = useState<
    CorrectionProject[]
  >([]);
  const [correctionSkills, setCorrectionSkills] = useState<CorrectionSkill[]>(
    [],
  );
  const emptyCorrectionEducation: CorrectionEducation = {
    school: "",
    degreeLevel: "",
    status: "unknown",
    major: "",
    startYear: "",
    graduationYear: "",
    sourceBlockIds: [],
  };
  const setCorrectionEducation = (
    index: number,
    updater: (value: CorrectionEducation) => CorrectionEducation,
  ) => {
    setCorrectionEducations((current) => {
      const base = current.length ? current : [emptyCorrectionEducation];
      return base.map((value, itemIndex) =>
        itemIndex === index ? updater(value) : value,
      );
    });
  };
  const correctionProject = correctionProjects[0] ?? {
    experienceUnitId: "",
    title: "",
    titleBlockIds: [],
    contextBlockIds: [],
    workEvidenceBlockIds: [],
    contextItems: [],
    sourceBullets: [],
  };
  const setCorrectionProject = (
    updater: (value: CorrectionProject) => CorrectionProject,
  ) => {
    setCorrectionProjects((current) => {
      const first = current[0] ?? correctionProject;
      const next = updater(first);
      return current.length ? [next, ...current.slice(1)] : [next];
    });
  };
  const correctionSkill = correctionSkills[0] ?? {
    name: "",
    details: "",
    sourceBlockIds: [],
  };
  const setCorrectionSkill = (
    updater: (value: CorrectionSkill) => CorrectionSkill,
  ) => {
    setCorrectionSkills((current) => {
      const first = current[0] ?? correctionSkill;
      const next = updater(first);
      return current.length ? [next, ...current.slice(1)] : [next];
    });
  };
  // 操作菜单使用 Portal 固定定位，避免被表格滚动容器裁切。
  const [actionMenu, setActionMenu] = useState<{
    submissionId: string;
    top: number;
    left: number;
    placement: "above" | "below";
    maxHeight: number;
  }>();
  const [duplicateAction, setDuplicateAction] = useState<{
    item: CandidateIntakeListItem;
    decision: "replace_resume" | "discard_submission";
  }>();
  const [duplicateIntake, setDuplicateIntake] = useState<CandidateIntake>();
  const [duplicateLoading, setDuplicateLoading] = useState(false);
  const [ambiguousTarget, setAmbiguousTarget] =
    useState<CandidateIntakeListItem>();
  const [ambiguousCandidates, setAmbiguousCandidates] = useState<
    DuplicateCandidateOption[]
  >([]);
  const [selectedCandidateId, setSelectedCandidateId] = useState("");
  const [ambiguousLoading, setAmbiguousLoading] = useState(false);
  const [routingTarget, setRoutingTarget] = useState<CandidateIntakeListItem>();
  const [routingOptions, setRoutingOptions] = useState<ManualRoutingOptions>();
  const [routingLoading, setRoutingLoading] = useState(false);
  const [timelineTarget, setTimelineTarget] =
    useState<CandidateIntakeListItem>();
  const [timelineEvents, setTimelineEvents] = useState<
    WorkflowExecutionEvent[]
  >([]);
  const [timelineLoading, setTimelineLoading] = useState(false);
  const [timelineError, setTimelineError] = useState("");
  const [selectedJobIds, setSelectedJobIds] = useState<string[]>([]);
  const previousBuckets = useRef<
    Map<string, CandidateIntakeListItem["bucket"]>
  >(new Map());
  const initialized = useRef(false);
  const pageSize = 20;
  const [selectedCandidateIds, setSelectedCandidateIds] = useState<Set<string>>(new Set());
  const [bulkDeleteOpen, setBulkDeleteOpen] = useState(false);
  const [bulkDeleting, setBulkDeleting] = useState(false);
  const [bulkDeleteProgress, setBulkDeleteProgress] = useState({ completed: 0, total: 0 });
  const intakeSelectAllRef = useRef<HTMLInputElement>(null);
  const canBulkDeleteCandidates = Boolean(
    user?.isSystemAdmin && user.businessScope === "organization",
  );
  const selectableCandidateIds = items.flatMap((item) =>
    item.candidateId ? [item.candidateId] : [],
  );
  const allCandidatesSelected =
    selectableCandidateIds.length > 0 &&
    selectableCandidateIds.every((candidateId) => selectedCandidateIds.has(candidateId));
  const someCandidatesSelected = selectedCandidateIds.size > 0 && !allCandidatesSelected;

  useEffect(() => {
    if (intakeSelectAllRef.current) {
      intakeSelectAllRef.current.indeterminate = someCandidatesSelected;
    }
  }, [someCandidatesSelected]);

  useEffect(() => {
    setSelectedCandidateIds(new Set());
  }, [status, keyword, page]);

  useEffect(() => {
    if (!targetSubmissionId) return;
    setStatus("all");
    setKeyword(targetSubmissionId);
    setPage(1);
  }, [targetSubmissionId]);

  const load = useCallback(
    async (silent = false) => {
      // 处理列表轮询时静默刷新；首次加载和用户操作完成后才显示整体加载状态。
      if (!token) return;
      if (!silent) setLoading(true);
      try {
        setError("");
        const view = await getCandidateIntakeList(token, {
          status,
          keyword,
          page,
          pageSize,
        });
        if (initialized.current) {
          view.items.forEach((item) => {
            const previous = previousBuckets.current.get(item.submissionId);
            if (previous === "processing" && item.bucket === "completed")
              toast.success(
                `${item.candidateName} 的简历已完成处理，已生成 ${item.applicationCount} 份岗位申请`,
              );
            if (previous === "processing" && item.bucket === "review_required")
              toast.warning(`${item.candidateName} 的简历需要确认`);
            if (previous === "processing" && item.bucket === "failed")
              toast.error(`${item.candidateName} 的简历处理失败`);
          });
        }
        previousBuckets.current = new Map(
          view.items.map((item) => [item.submissionId, item.bucket]),
        );
        initialized.current = true;
        setItems(view.items);
        setTotal(view.total);
        onUnreadCountChange?.(view.counts.unreadAttentionCount);
      } catch (reason) {
        setError(
          reason instanceof Error ? reason.message : "简历处理任务加载失败",
        );
      } finally {
        if (!silent) setLoading(false);
      }
    },
    [keyword, onUnreadCountChange, page, status, token, toast],
  );

  useEffect(() => {
    void load();
  }, [load, refreshVersion]);
  usePeriodicRefresh(
    () => load(true),
    // WorkflowRun 刚创建、尚未写入首个检查点时 process 可能为空；
    // ResumeSubmission 的 processing 桶仍是可靠的轮询依据。
    items.some(
      (item) =>
        item.bucket === "processing" || isWorkflowProcessActive(item.process),
    ),
  );

  useEffect(() => {
    if (!token) return;
    void markCandidateIntakesRead(token)
      .then(() => onUnreadCountChange?.(0))
      .catch(() => undefined);
  }, [onUnreadCountChange, token]);

  const loadTimeline = useCallback(async () => {
    if (!token || !timelineTarget) return;
    setTimelineLoading(true);
    setTimelineError("");
    try {
      const view = await getCandidateIntakeWorkflowTimeline(
        token,
        timelineTarget.submissionId,
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
    if (
      !timelineTarget ||
      !(
        timelineTarget.bucket === "processing" ||
        isWorkflowProcessActive(timelineTarget.process)
      )
    )
      return;
    const timer = window.setInterval(() => void loadTimeline(), 5000);
    return () => window.clearInterval(timer);
  }, [loadTimeline, timelineTarget]);

  const openHardScreeningResult = async (
    application: CandidateIntakeListItem["applications"][number],
  ) => {
    if (!token) return;
    setApplicationsTarget(undefined);
    setHardScreeningTarget(application);
    setHardScreeningResult(undefined);
    setHardScreeningError("");
    setHardScreeningLoading(true);
    try {
      setHardScreeningResult(
        await getHardScreeningResult(token, application.applicationId),
      );
    } catch (reason) {
      setHardScreeningError(
        reason instanceof Error ? reason.message : "硬筛结果加载失败",
      );
    } finally {
      setHardScreeningLoading(false);
    }
  };

  // 重新解析沿用当前 PDF，只创建新的候选人级处理任务。
  const rebuild = async (item: CandidateIntakeListItem) => {
    if (!token) return;
    setWorkingId(item.submissionId);
    try {
      await rebuildCandidateFromResume(token, item.submissionId);
      toast.info(
        "已提交重新解析，系统会用新版本简历重建该候选人的岗位申请数据",
      );
      await load();
    } catch (reason) {
      toast.error(
        reason instanceof Error ? reason.message : "重新解析提交失败",
      );
    } finally {
      setWorkingId("");
    }
  };

  const retryPublish = async (item: CandidateIntakeListItem) => {
    if (!token) return;
    setWorkingId(item.submissionId);
    try {
      await retryCandidateResumePublish(token, item.submissionId);
      toast.info("已提交重新发布，系统会复用已有解析结果");
      await load();
    } catch (reason) {
      toast.error(
        reason instanceof Error ? reason.message : "重新发布提交失败",
      );
    } finally {
      setWorkingId("");
    }
  };

  const retryRouting = async (item: CandidateIntakeListItem) => {
    if (!token) return;
    setWorkingId(item.submissionId);
    try {
      await retryCandidateRouting(token, item.submissionId);
      toast.info("已重新提交岗位匹配");
      await load();
    } catch (reason) {
      toast.error(
        reason instanceof Error ? reason.message : "岗位匹配重试提交失败",
      );
    } finally {
      setWorkingId("");
    }
  };

  const openCorrection = async (item: CandidateIntakeListItem) => {
    if (!token) return;
    setCorrectionTarget(item);
    setCorrectionDraft(undefined);
    setCorrectionEducations([]);
    setCorrectionExperienceYears({ value: "", sourceBlockIds: [] });
    setCorrectionProjects([]);
    setCorrectionSkills([]);
    setCorrectionLoading(true);
    try {
      const draft = await getResumeCorrectionDraft(token, item.submissionId);
      setCorrectionDraft(draft);
      const facts = draft.candidateFacts as {
        education_records?: Array<{
          school?: string;
          degree_level?: string;
          status?: string;
          major?: string;
          start_year?: number;
          graduation_year?: number;
          source_refs?: Array<{ block_id?: string }>;
        }>;
        relevant_experience_years?: {
          value?: number;
          source_refs?: Array<{ block_id?: string }>;
        };
      };
      const skills = draft.skillClaims as Array<{
        skill_name?: string;
        details?: string[];
        source_refs?: Array<{ block_id?: string }>;
      }>;
      const refIds = (refs: Array<{ block_id?: string }> | undefined) =>
        (refs ?? []).map((ref) => String(ref.block_id ?? "")).filter(Boolean);
      setCorrectionEducations(
        (facts.education_records ?? []).map((education) => ({
          school: education.school ?? "",
          degreeLevel: education.degree_level ?? "",
          status: education.status ?? "unknown",
          major: education.major ?? "",
          startYear:
            education.start_year === undefined
              ? ""
              : String(education.start_year),
          graduationYear:
            education.graduation_year === undefined
              ? ""
              : String(education.graduation_year),
          sourceBlockIds: refIds(education.source_refs),
        })),
      );
      setCorrectionExperienceYears({
        value:
          facts.relevant_experience_years?.value === undefined
            ? ""
            : String(facts.relevant_experience_years.value),
        sourceBlockIds: refIds(
          facts.relevant_experience_years?.source_refs,
        ),
      });
      setCorrectionProjects(
        draft.experienceUnits.map((item) => ({
          experienceUnitId: item.experienceUnitId,
          title: item.title,
          titleBlockIds: refIds(item.titleSourceRefs as Array<{ block_id?: string }>),
          contextBlockIds: refIds(item.contextSourceRefs as Array<{ block_id?: string }>),
          workEvidenceBlockIds: refIds(item.workSourceRefs as Array<{ block_id?: string }>),
          contextItems: item.contextItems,
          sourceBullets: item.sourceBullets,
        })),
      );
      setCorrectionSkills(
        skills.map((item) => ({
          name: item.skill_name ?? "",
          details: (item.details ?? []).join("、"),
          sourceBlockIds: refIds(item.source_refs),
        })),
      );
    } catch (reason) {
      toast.error(
        reason instanceof Error ? reason.message : "校正草稿加载失败",
      );
      setCorrectionTarget(undefined);
    } finally {
      setCorrectionLoading(false);
    }
  };

  const submitCorrection = async () => {
    if (!token || !correctionTarget) {
      return;
    }
    const sourceTextById = new Map(
      (correctionDraft?.sourceBlocks ?? []).map((block) => [
        block.blockId,
        block.text,
      ]),
    );
    const projects = correctionProjects
      .map((item) => ({ ...item, title: item.title.trim() }))
      .filter((item) => item.title);
    const educations = correctionEducations
      .map((item) => ({
        ...item,
        school: item.school.trim(),
        degreeLevel: item.degreeLevel.trim(),
        major: item.major.trim(),
        startYear: item.startYear.trim(),
        graduationYear: item.graduationYear.trim(),
      }))
      .filter((item) =>
        Boolean(
          item.school || item.degreeLevel || item.major || item.startYear || item.graduationYear,
        ),
      );
    const skills = correctionSkills
      .map((item) => ({ ...item, name: item.name.trim() }))
      .filter((item) => item.name);
    if (
      projects.some(
        (item) =>
          item.titleBlockIds.length === 0 ||
          item.workEvidenceBlockIds.length === 0,
      )
    ) {
      toast.warning("每个项目都必须选择标题原文和至少一段直接工作证据");
      return;
    }
    if (
      projects.some(
        (item) =>
          !item.titleBlockIds.some((id) =>
            (sourceTextById.get(id) ?? "").includes(item.title),
          ),
      )
    ) {
      toast.warning("项目标题必须出现在所选标题原文片段中");
      return;
    }
    if (skills.some((item) => item.sourceBlockIds.length === 0)) {
      toast.warning("每个技能都必须选择对应的原文片段");
      return;
    }
    if (
      educations.some((item) => item.sourceBlockIds.length === 0) ||
      (correctionExperienceYears.value.trim() &&
        correctionExperienceYears.sourceBlockIds.length === 0)
    ) {
      toast.warning("每项教育和经验年限都必须选择对应的原文片段");
      return;
    }
    if (
      educations.some(
        (item) =>
          (item.startYear && !/^\d{4}$/.test(item.startYear)) ||
          (item.graduationYear && !/^\d{4}$/.test(item.graduationYear)),
      )
    ) {
      toast.warning("入学和毕业年份必须是四位年份");
      return;
    }
    const experienceYears = correctionExperienceYears.value.trim();
    if (
      experienceYears &&
      (!Number.isFinite(Number(experienceYears)) || Number(experienceYears) < 0)
    ) {
      toast.warning("相关经验年限必须是大于或等于 0 的数字");
      return;
    }
    setWorkingId(correctionTarget.submissionId);
    try {
      await submitResumeManualCorrection(token, correctionTarget.submissionId, {
        candidateFacts: {
          education_records: educations.map((item) => ({
            school: item.school,
            degree_level: item.degreeLevel,
            status: item.status,
            major: item.major,
            start_year: item.startYear ? Number(item.startYear) : undefined,
            graduation_year: item.graduationYear
              ? Number(item.graduationYear)
              : undefined,
            source_block_ids: item.sourceBlockIds,
          })),
          relevant_experience_years: {
            value: experienceYears ? Number(experienceYears) : undefined,
            source_block_ids: correctionExperienceYears.sourceBlockIds,
          },
        },
        experienceUnits: projects.map((item) => ({
          experience_unit_id: item.experienceUnitId,
          title: item.title,
          title_block_ids: item.titleBlockIds,
          context_block_ids: item.contextBlockIds,
          work_evidence_block_ids: item.workEvidenceBlockIds,
        })),
        skillClaims: skills.map((item) => ({
          skill_name: item.name,
          details: item.details
            .split(/[、,，]/)
            .map((value) => value.trim())
            .filter(Boolean),
          source_block_ids: item.sourceBlockIds,
        })),
      });
      toast.success("已提交校正结果，系统会基于所选原文重新生成简历画像");
      setCorrectionTarget(undefined);
      setCorrectionDraft(undefined);
      await load();
    } catch (reason) {
      toast.error(
        reason instanceof Error ? reason.message : "校正结果提交失败",
      );
    } finally {
      setWorkingId("");
    }
  };

  // 删除以 Candidate 为边界，而非删除单次 Submission；避免简历处理页留下孤儿条目。
  const removeCandidate = async (item: CandidateIntakeListItem) => {
    if (!token || !item.candidateId) return;
    const message =
      item.applicationCount > 0
        ? `确认删除“${item.candidateName}”吗？该候选人的 ${item.applicationCount} 个岗位申请及进行中的处理任务也会从业务列表移除。历史记录仍保留用于审计。`
        : `确认删除“${item.candidateName}”吗？历史记录仍保留用于审计。`;
    if (!window.confirm(message)) return;
    setWorkingId(item.submissionId);
    try {
      await deleteCandidate(token, item.candidateId);
      toast.success("候选人已删除");
      await load();
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : "删除候选人失败");
    } finally {
      setWorkingId("");
    }
  };

  const confirmBulkDeleteCandidates = async () => {
    const candidateIds = Array.from(selectedCandidateIds);
    if (!token || bulkDeleting || candidateIds.length === 0) return;
    setBulkDeleting(true);
    setBulkDeleteProgress({ completed: 0, total: candidateIds.length });
    const successIds: string[] = [];
    const failedIds: string[] = [];
    let nextIndex = 0;
    const runner = async () => {
      while (nextIndex < candidateIds.length) {
        const candidateId = candidateIds[nextIndex];
        nextIndex += 1;
        try {
          await deleteCandidate(token, candidateId);
          successIds.push(candidateId);
        } catch {
          failedIds.push(candidateId);
        } finally {
          setBulkDeleteProgress((current) => ({
            ...current,
            completed: current.completed + 1,
          }));
        }
      }
    };
    try {
      await Promise.all(
        Array.from({ length: Math.min(3, candidateIds.length) }, () => runner()),
      );
      setSelectedCandidateIds(new Set(failedIds));
      setBulkDeleteOpen(false);
      if (successIds.length > 0) await load();
      if (failedIds.length > 0) {
        toast.warning(
          successIds.length > 0
            ? `已删除${successIds.length}位候选人，${failedIds.length}位删除失败`
            : `${failedIds.length}位候选人删除失败`,
        );
      } else {
        toast.success(`已删除${successIds.length}位候选人`);
      }
    } finally {
      setBulkDeleting(false);
    }
  };
  const chooseReplacement = (item: CandidateIntakeListItem) => {
    setReplacementTarget(item);
    replacementInputRef.current?.click();
  };

  const uploadReplacement = async (file?: File) => {
    // 重新上传仅替换 PDF，其后的解析、结构化、Application 重建与重新解析共用同一条链路。
    if (!token || !replacementTarget || !file) return;
    setWorkingId(replacementTarget.submissionId);
    try {
      await replaceCandidateResume(token, replacementTarget.submissionId, file);
      toast.info("新版简历已上传，系统会重新解析并重建该候选人的岗位申请数据");
      await load();
    } catch (reason) {
      toast.error(
        reason instanceof Error ? reason.message : "新版简历上传失败",
      );
    } finally {
      setWorkingId("");
      setReplacementTarget(undefined);
      if (replacementInputRef.current) replacementInputRef.current.value = "";
    }
  };

  const openDuplicateDecision = async (
    item: CandidateIntakeListItem,
    decision: "replace_resume" | "discard_submission",
  ) => {
    if (!token) return;
    setDuplicateAction({ item, decision });
    setDuplicateIntake(undefined);
    setDuplicateLoading(true);
    try {
      setDuplicateIntake(await getCandidateIntake(token, item.submissionId));
    } catch (reason) {
      toast.error(
        reason instanceof Error ? reason.message : "重复候选人信息加载失败",
      );
      setDuplicateAction(undefined);
      setDuplicateIntake(undefined);
    } finally {
      setDuplicateLoading(false);
    }
  };

  const submitDuplicateDecision = async () => {
    if (!token || !duplicateAction) return;
    const { item, decision } = duplicateAction;
    setWorkingId(item.submissionId);
    try {
      await decideResumeDuplicate(token, item.submissionId, decision);
      toast.success(
        decision === "replace_resume"
          ? "已采用新简历并重新进入处理流程"
          : "已放弃本次重复简历导入",
      );
      setDuplicateAction(undefined);
      setDuplicateIntake(undefined);
      await load();
    } catch (reason) {
      toast.error(
        reason instanceof Error ? reason.message : "处理重复简历失败",
      );
    } finally {
      setWorkingId("");
    }
  };

  const openManualRouting = async (item: CandidateIntakeListItem) => {
    if (!token) return;
    setRoutingTarget(item);
    setRoutingOptions(undefined);
    setSelectedJobIds([]);
    setRoutingLoading(true);
    try {
      setRoutingOptions(
        await getManualRoutingOptions(token, item.submissionId),
      );
    } catch (reason) {
      toast.error(
        reason instanceof Error ? reason.message : "可选岗位加载失败",
      );
      setRoutingTarget(undefined);
    } finally {
      setRoutingLoading(false);
    }
  };

  const submitManualRouting = async () => {
    if (!token || !routingTarget || selectedJobIds.length === 0) return;
    setWorkingId(routingTarget.submissionId);
    try {
      const result = await createApplicationsFromIntake(
        token,
        routingTarget.submissionId,
        selectedJobIds,
      );
      toast.success(
        "已创建 " +
          result.application_ids.length +
          " 个岗位申请；岗位画像已就绪的申请会进入初步筛选，其余申请将等待画像完成。",
      );
      setRoutingTarget(undefined);
      setRoutingOptions(undefined);
      setSelectedJobIds([]);
      await load();
    } catch (reason) {
      toast.error(
        reason instanceof Error ? reason.message : "创建岗位申请失败",
      );
    } finally {
      setWorkingId("");
    }
  };
  const openAmbiguousDuplicateResolution = async (
    item: CandidateIntakeListItem,
  ) => {
    if (!token) return;
    setAmbiguousTarget(item);
    setAmbiguousCandidates([]);
    setSelectedCandidateId("");
    setAmbiguousLoading(true);
    try {
      setAmbiguousCandidates(
        await getAmbiguousDuplicateCandidates(token, item.submissionId),
      );
    } catch (reason) {
      toast.error(
        reason instanceof Error ? reason.message : "候选人匹配项加载失败",
      );
      setAmbiguousTarget(undefined);
    } finally {
      setAmbiguousLoading(false);
    }
  };

  const resolveAmbiguousDuplicateDecision = async (
    decision:
      "merge_into_candidate" | "create_new_candidate" | "discard_submission",
  ) => {
    if (!token || !ambiguousTarget) return;
    if (decision === "merge_into_candidate" && !selectedCandidateId) {
      toast.warning("请选择要合并到的候选人");
      return;
    }
    setWorkingId(ambiguousTarget.submissionId);
    try {
      await resolveAmbiguousDuplicate(token, ambiguousTarget.submissionId, {
        decision,
        targetCandidateId:
          decision === "merge_into_candidate" ? selectedCandidateId : undefined,
      });
      toast.success(
        decision === "merge_into_candidate"
          ? "已采用所选候选人的新简历"
          : decision === "create_new_candidate"
            ? "已作为新候选人继续岗位分发"
            : "已放弃本次简历导入",
      );
      setAmbiguousTarget(undefined);
      setAmbiguousCandidates([]);
      setSelectedCandidateId("");
      await load();
    } catch (reason) {
      toast.error(
        reason instanceof Error ? reason.message : "重复候选人处理失败",
      );
    } finally {
      setWorkingId("");
    }
  };
  return (
    <section className="rounded-lg border border-line bg-white">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-5 py-4">
        <div>
          <h2 className="font-semibold text-ink">简历处理</h2>
          <p className="mt-1 text-sm text-muted">
            查看简历从解析、结构化到创建岗位申请的处理记录。
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {filters.map((item) => (
            <Button
              key={item.value}
              className="h-8 px-2.5 text-xs"
              variant={status === item.value ? "primary" : "ghost"}
              onClick={() => {
                setStatus(item.value);
                setPage(1);
              }}
            >
              {item.label}
            </Button>
          ))}
          <input
            className="h-8 w-48 rounded-md border border-line px-2.5 text-xs outline-none focus:border-blue-500"
            value={keyword}
            onChange={(event) => {
              setKeyword(event.target.value);
              setPage(1);
            }}
            placeholder="搜索姓名、文件或岗位"
          />
        </div>
      </div>
      {canBulkDeleteCandidates && selectedCandidateIds.size > 0 ? (
        <div className="mx-4 mt-3 flex flex-wrap items-center justify-between gap-3 rounded-md border border-blue-200 bg-blue-50 px-3 py-2.5">
          <span className="text-sm font-medium text-blue-900">
            已选择 {selectedCandidateIds.size} 位候选人
          </span>
          <div className="flex items-center gap-2">
            <Button
              className="h-8 px-2.5 text-xs"
              onClick={() => setSelectedCandidateIds(new Set())}
              disabled={bulkDeleting}
            >
              清除选择
            </Button>
            <Button
              className="h-8 px-2.5 text-xs"
              variant="danger"
              onClick={() => {
                setBulkDeleteProgress({ completed: 0, total: selectedCandidateIds.size });
                setBulkDeleteOpen(true);
              }}
              disabled={bulkDeleting}
            >
              <Trash2 size={14} />
              删除所选
            </Button>
          </div>
        </div>
      ) : null}
      {error ? (
        <div className="m-4 rounded-md bg-rose-50 p-3 text-sm text-rose-800">
          {error}
        </div>
      ) : null}
      <div className="overflow-x-auto">
        <table className="w-full min-w-[984px] text-left text-sm">
          <thead className="bg-slate-50 text-xs font-medium text-slate-500">
            <tr>
              {canBulkDeleteCandidates ? (
                <th className="px-3 py-3">
                  <input
                    ref={intakeSelectAllRef}
                    type="checkbox"
                    aria-label="选择当前页全部候选人"
                    checked={allCandidatesSelected}
                    disabled={bulkDeleting}
                    onChange={(event) => {
                      setSelectedCandidateIds(
                        event.target.checked
                          ? new Set(selectableCandidateIds)
                          : new Set(),
                      );
                    }}
                  />
                </th>
              ) : null}
              <th className="px-5 py-3">候选人 / 简历</th>
              <th className="px-4 py-3">当前阶段</th>
              <th className="px-4 py-3">岗位匹配结果</th>
              <th className="px-4 py-3">更新时间</th>
              <th className="px-5 py-3 text-right">操作</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {items.map((item) => (
              <tr
                key={item.submissionId}
                className={
                  item.submissionId === targetSubmissionId ? "bg-blue-50" : undefined
                }
              >
                {canBulkDeleteCandidates ? (
                  <td className="px-3 py-3 align-top">
                    {item.candidateId ? (
                      <input
                        type="checkbox"
                        aria-label={`选择${item.candidateName}`}
                        checked={selectedCandidateIds.has(item.candidateId)}
                        disabled={bulkDeleting}
                        onChange={(event) => {
                          setSelectedCandidateIds((current) => {
                            const next = new Set(current);
                            if (event.target.checked) next.add(item.candidateId!);
                            else next.delete(item.candidateId!);
                            return next;
                          });
                        }}
                      />
                    ) : null}
                  </td>
                ) : null}
                <td className="px-5 py-3">
                  <div className="font-medium text-ink">
                    {item.candidateName}
                  </div>
                  {item.candidateMajor || item.resumeProfileId ? (
                    <div className="mt-0.5 max-w-72 truncate text-xs text-slate-600">
                      {item.candidateMajor || "简历画像已生成"}
                      {item.resumeProfileId ? " · 已关联简历画像" : ""}
                    </div>
                  ) : null}
                  <div className="mt-0.5 max-w-72 truncate text-xs text-muted">
                    {item.filename}
                  </div>
                  {item.resumePdfUrl ? <AuthenticatedPdfLink url={item.resumePdfUrl} /> : null}
                </td>
                <td className="px-4 py-3">
                  <CandidateIntakeStageCell item={item} />
                </td>
                <td className="px-4 py-3">
                  {item.applications.length ? (
                    <div className="space-y-1">
                      <button
                        type="button"
                        className="inline-flex items-center gap-0.5 text-xs font-medium text-blue-700 transition hover:text-blue-900"
                        onClick={() => setApplicationsTarget(item)}
                      >
                        已创建 {item.applicationCount} 个申请{" "}
                        <ChevronRight size={14} aria-hidden="true" />
                      </button>
                      <div
                        className="max-w-56 truncate text-xs text-slate-600"
                        title={item.applications
                          .map((app) => app.jobTitle)
                          .join("、")}
                      >
                        {item.applications
                          .slice(0, 2)
                          .map((app) => app.jobTitle)
                          .join("、")}
                        {item.applications.length > 2 ? " 等" : ""}
                      </div>
                    </div>
                  ) : item.routingStatus === "manual_selection_available" &&
                    hasRecoveryAction(item.availableActions, "create_applications") ? (
                    <button
                      type="button"
                      className="inline-flex items-center gap-1 text-xs font-medium text-blue-700 transition hover:text-blue-900"
                      disabled={workingId === item.submissionId}
                      onClick={() => void openManualRouting(item)}
                    >
                      <CheckCircle2 size={14} />
                      人工选择岗位
                    </button>
                  ) : item.bucket === "processing" ? (
                    <span className="text-xs text-muted">等待匹配完成</span>
                  ) : (
                    <span className="text-xs text-muted">尚未创建岗位申请</span>
                  )}
                </td>
                <td className="px-4 py-3 text-xs text-muted">
                  {formatTime(item.updatedAt)}
                </td>
                <td className="px-5 py-3 text-right">
                  <div className="relative inline-block text-left">
                    <button
                      type="button"
                      className={`inline-flex h-8 w-8 items-center justify-center rounded-md ${actionMenu?.submissionId === item.submissionId ? "bg-slate-100 text-slate-800" : "text-slate-500 hover:bg-slate-100 hover:text-slate-800"}`}
                      aria-label={`${item.candidateName} 的更多操作`}
                      aria-haspopup="menu"
                      aria-expanded={
                        actionMenu?.submissionId === item.submissionId
                      }
                      onClick={(event) => {
                        if (actionMenu?.submissionId === item.submissionId) {
                          setActionMenu(undefined);
                          return;
                        }
                        const rect =
                          event.currentTarget.getBoundingClientRect();
                        const menuWidth = 192;
                        const menuItemCount =
                          Number(item.sourceAvailable) +
                          Number(Boolean(item.workflowRunId)) +
                          [
                            "retry_publish_resume",
                            "retry_routing",
                            "create_applications",
                            "resolve_duplicate_candidates",
                            "replace_duplicate_resume",
                            "discard_submission",
                            "force_fresh_parse",
                            "correct_parsed_resume",
                            "upload_replacement_resume",
                          ].filter(
                            (action) =>
                              hasRecoveryAction(item.availableActions, action) &&
                              (action !== "replace_duplicate_resume" ||
                                isDuplicateResumeReview(item)),
                          ).length +
                          Number(
                            Boolean(
                              user?.isSystemAdmin
                              && user.businessScope === "organization"
                              && item.candidateId,
                            ),
                          ) + Number(Boolean(item.candidateId));
                        const menuHeight = Math.max(
                          48,
                          menuItemCount * 44 + 12,
                        );
                        const availableBelow =
                          window.innerHeight - rect.bottom - 12;
                        const availableAbove = rect.top - 12;
                        const placement =
                          availableBelow >= menuHeight ||
                          availableBelow >= availableAbove
                            ? "below"
                            : "above";
                        const left = Math.max(
                          8,
                          Math.min(
                            rect.right - menuWidth,
                            window.innerWidth - menuWidth - 8,
                          ),
                        );
                        setActionMenu({
                          submissionId: item.submissionId,
                          top:
                            placement === "below"
                              ? rect.bottom + 4
                              : rect.top - 4,
                          left,
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
                    {actionMenu?.submissionId === item.submissionId
                      ? createPortal(
                          <div
                            role="menu"
                            aria-label={`${item.candidateName} 的更多操作`}
                            className="fixed z-50 max-h-[calc(100vh-16px)] w-48 overflow-y-auto rounded-lg border border-slate-200 bg-white p-1.5 shadow-lg shadow-slate-900/10"
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
                            {item.candidateId && item.candidateDocumentsAvailable ? (
                              <button
                                type="button"
                                role="menuitem"
                                className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm text-slate-700 transition hover:bg-emerald-50 hover:text-emerald-800"
                                onClick={() => {
                                  setActionMenu(undefined);
                                  setCandidateDocumentTarget(item);
                                }}
                              >
                                <FileText size={15} />
                                候选人资料
                              </button>
                            ) : null}
                            {item.workflowRunId ? (
                              <button
                                type="button"
                                role="menuitem"
                                className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm text-slate-700 transition hover:bg-slate-50 hover:text-slate-900"
                                onClick={() => {
                                  setActionMenu(undefined);
                                  setTimelineTarget(item);
                                }}
                              >
                                <Eye size={15} />
                                查看执行轨迹
                              </button>
                            ) : null}
                            {hasRecoveryAction(item.availableActions,
                              "retry_publish_resume",
                            ) ? (
                              <button
                                type="button"
                                role="menuitem"
                                className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm text-slate-700 transition hover:bg-slate-50 hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-50"
                                disabled={workingId === item.submissionId}
                                onClick={() => {
                                  setActionMenu(undefined);
                                  void retryPublish(item);
                                }}
                              >
                                <RefreshCw size={15} />
                                {recoveryAction(item.availableActions, "retry_publish_resume")?.label}
                              </button>
                            ) : null}
                            {hasRecoveryAction(item.availableActions, "retry_routing") ? (
                              <button
                                type="button"
                                role="menuitem"
                                className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm text-slate-700 transition hover:bg-slate-50 hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-50"
                                disabled={workingId === item.submissionId}
                                onClick={() => {
                                  setActionMenu(undefined);
                                  void retryRouting(item);
                                }}
                              >
                                <RefreshCw size={15} />
                                {recoveryAction(item.availableActions, "retry_routing")?.label}
                              </button>
                            ) : null}
                            {hasRecoveryAction(item.availableActions,
                              "create_applications",
                            ) ? (
                              <button
                                type="button"
                                role="menuitem"
                                className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm text-slate-700 transition hover:bg-slate-50 hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-50"
                                disabled={workingId === item.submissionId}
                                onClick={() => {
                                  setActionMenu(undefined);
                                  void openManualRouting(item);
                                }}
                              >
                                <CheckCircle2 size={15} />
                                {recoveryAction(item.availableActions, "create_applications")?.label}
                              </button>
                            ) : null}
                            {hasRecoveryAction(item.availableActions,
                              "resolve_duplicate_candidates",
                            ) ? (
                              <button
                                type="button"
                                role="menuitem"
                                className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm text-slate-700 transition hover:bg-slate-50 hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-50"
                                disabled={workingId === item.submissionId}
                                onClick={() => {
                                  setActionMenu(undefined);
                                  void openAmbiguousDuplicateResolution(item);
                                }}
                              >
                                <CheckCircle2 size={15} />
                                {recoveryAction(item.availableActions, "resolve_duplicate_candidates")?.label}
                              </button>
                            ) : null}
                            {hasRecoveryAction(item.availableActions,
                              "replace_duplicate_resume",
                            ) && isDuplicateResumeReview(item) ? (
                              <button
                                type="button"
                                role="menuitem"
                                className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm text-slate-700 transition hover:bg-slate-50 hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-50"
                                onClick={() => {
                                  setActionMenu(undefined);
                                  void openDuplicateDecision(
                                    item,
                                    "replace_resume",
                                  );
                                }}
                              >
                                {recoveryAction(item.availableActions, "replace_duplicate_resume")?.label}
                              </button>
                            ) : null}
                            {hasRecoveryAction(item.availableActions,
                              "discard_submission",
                            ) ? (
                              <button
                                type="button"
                                role="menuitem"
                                className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm text-slate-700 transition hover:bg-slate-50 hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-50"
                                onClick={() => {
                                  setActionMenu(undefined);
                                  void openDuplicateDecision(
                                    item,
                                    "discard_submission",
                                  );
                                }}
                              >
                                {recoveryAction(item.availableActions, "discard_submission")?.label}
                              </button>
                            ) : null}
                            {hasRecoveryAction(item.availableActions,
                              "force_fresh_parse",
                            ) ? (
                              <button
                                type="button"
                                role="menuitem"
                                className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm text-slate-700 transition hover:bg-slate-50 hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-50"
                                disabled={workingId === item.submissionId}
                                onClick={() => {
                                  setActionMenu(undefined);
                                  void rebuild(item);
                                }}
                              >
                                <RefreshCw size={15} />
                                {recoveryAction(item.availableActions, "force_fresh_parse")?.label}
                              </button>
                            ) : null}
                            {hasRecoveryAction(item.availableActions,
                              "correct_parsed_resume",
                            ) ? (
                              <button
                                type="button"
                                role="menuitem"
                                className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm text-slate-700 transition hover:bg-slate-50 hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-50"
                                disabled={workingId === item.submissionId}
                                onClick={() => {
                                  setActionMenu(undefined);
                                  void openCorrection(item);
                                }}
                              >
                                <FileText size={15} />
                                {recoveryAction(item.availableActions, "correct_parsed_resume")?.label}
                              </button>
                            ) : null}
                            {hasRecoveryAction(item.availableActions,
                              "upload_replacement_resume",
                            ) ? (
                              <button
                                type="button"
                                role="menuitem"
                                className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm text-slate-700 transition hover:bg-slate-50 hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-50"
                                onClick={() => {
                                  setActionMenu(undefined);
                                  chooseReplacement(item);
                                }}
                              >
                                <Upload size={15} />
                                {recoveryAction(item.availableActions, "upload_replacement_resume")?.label}
                              </button>
                            ) : null}
                            {user?.isSystemAdmin && user.businessScope === "organization" && item.candidateId ? (
                              <button
                                type="button"
                                role="menuitem"
                                className="mt-1 flex w-full items-center gap-2 rounded-md border-t border-slate-100 px-2.5 py-2 text-left text-sm text-rose-600 transition hover:bg-rose-50 disabled:cursor-not-allowed disabled:opacity-50"
                                disabled={workingId === item.submissionId}
                                onClick={() => {
                                  setActionMenu(undefined);
                                  void removeCandidate(item);
                                }}
                              >
                                <Trash2 size={15} />
                                删除候选人
                              </button>
                            ) : null}
                          </div>,
                          document.body,
                        )
                      : null}
                  </div>
                </td>
              </tr>
            ))}
            {!loading && !items.length ? (
              <tr>
                <td
                  colSpan={canBulkDeleteCandidates ? 6 : 5}
                  className="px-5 py-12 text-center text-sm text-muted"
                >
                  <FileClock className="mx-auto mb-2" size={20} />
                  暂无符合条件的简历处理记录。
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
      {loading ? (
        <div className="px-5 py-4 text-sm text-muted">
          正在加载简历处理记录…
        </div>
      ) : null}
      {total > pageSize ? (
        <div className="flex items-center justify-end gap-2 border-t border-line px-5 py-3 text-xs text-muted">
          <span>共 {total} 条</span>
          <Button
            className="h-7 px-2"
            disabled={page === 1}
            onClick={() => setPage((value) => value - 1)}
          >
            <ChevronLeft size={14} />
          </Button>
          <span>{page}</span>
          <Button
            className="h-7 px-2"
            disabled={page * pageSize >= total}
            onClick={() => setPage((value) => value + 1)}
          >
            <ChevronRight size={14} />
          </Button>
        </div>
      ) : null}
      <input
        ref={replacementInputRef}
        className="hidden"
        type="file"
        accept="application/pdf,.pdf"
        onChange={(event) => void uploadReplacement(event.target.files?.[0])}
      />
      {bulkDeleteOpen ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="candidate-bulk-delete-title"
        >
          <div className="w-full max-w-lg rounded-lg bg-white p-5 shadow-xl">
            <h3 id="candidate-bulk-delete-title" className="font-semibold text-ink">
              删除 {selectedCandidateIds.size} 位候选人
            </h3>
            <p className="mt-3 text-sm leading-6 text-slate-700">
              确认删除已选择的候选人吗？这会同时移除其全部岗位申请，并取消仍在进行中的处理任务。
            </p>
            <div className="mt-3 rounded-md border border-line bg-slate-50 px-3 py-2 text-sm text-slate-700">
              {items
                .filter((item) =>
                  item.candidateId
                    ? selectedCandidateIds.has(item.candidateId)
                    : false,
                )
                .slice(0, 3)
                .map((item) => (
                  <div key={item.candidateId} className="truncate py-0.5">
                    {item.candidateName} · {item.filename}
                  </div>
                ))}
              {selectedCandidateIds.size > 3 ? (
                <div className="pt-0.5 text-xs text-muted">
                  另有 {selectedCandidateIds.size - 3} 位候选人
                </div>
              ) : null}
            </div>
            <p className="mt-3 rounded-md bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-800">
              删除属于归档操作，历史记录仍会保留用于审计。
            </p>
            {bulkDeleting ? (
              <p className="mt-3 text-sm text-muted" aria-live="polite">
                正在删除 {bulkDeleteProgress.completed}/{bulkDeleteProgress.total}
              </p>
            ) : null}
            <div className="mt-4 flex justify-end gap-2">
              <Button
                disabled={bulkDeleting}
                onClick={() => setBulkDeleteOpen(false)}
              >
                取消
              </Button>
              <Button
                variant="danger"
                disabled={bulkDeleting}
                onClick={() => void confirmBulkDeleteCandidates()}
              >
                {bulkDeleting
                  ? "正在删除"
                  : `删除 ${selectedCandidateIds.size} 位候选人`}
              </Button>
            </div>
          </div>
        </div>
      ) : null}
      {timelineTarget ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="candidate-workflow-timeline-title"
        >
          <div className="flex max-h-[86vh] w-full max-w-2xl flex-col overflow-hidden rounded-lg bg-white shadow-xl">
            <div className="flex items-start justify-between gap-4 border-b border-line p-5">
              <div>
                <h3
                  id="candidate-workflow-timeline-title"
                  className="font-semibold text-ink"
                >
                  简历处理轨迹
                </h3>
                <p className="mt-1 text-sm text-muted">
                  {timelineTarget.candidateName} · {timelineTarget.filename}
                </p>
              </div>
              <button
                type="button"
                className="text-sm text-muted hover:text-ink"
                onClick={() => setTimelineTarget(undefined)}
              >
                关闭
              </button>
            </div>
            <div className="min-h-0 flex-1 overflow-auto p-5">
              <WorkflowExecutionTimeline
                events={timelineEvents}
                loading={timelineLoading}
                error={timelineError}
              />
            </div>
            <div className="flex justify-end border-t border-line p-4">
              <Button onClick={() => setTimelineTarget(undefined)}>关闭</Button>
            </div>
          </div>
        </div>
      ) : null}
      {candidateDocumentTarget?.candidateId ? (
        <CandidateDocumentLibrary
          open
          candidateId={candidateDocumentTarget.candidateId}
          candidateName={candidateDocumentTarget.candidateName}
          onClose={() => setCandidateDocumentTarget(undefined)}
        />
      ) : null}
      {applicationsTarget ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="candidate-applications-title"
        >
          <div className="flex max-h-[86vh] w-full max-w-2xl flex-col overflow-hidden rounded-lg bg-white shadow-xl">
            <div className="flex items-start justify-between gap-4 border-b border-line p-5">
              <div>
                <h3
                  id="candidate-applications-title"
                  className="font-semibold text-ink"
                >
                  已创建的岗位申请
                </h3>
                <p className="mt-1 text-sm text-muted">
                  “{applicationsTarget.candidateName}”当前已创建{" "}
                  {applicationsTarget.applicationCount} 个岗位申请。
                </p>
              </div>
              <button
                type="button"
                className="text-sm text-muted transition hover:text-ink"
                onClick={() => setApplicationsTarget(undefined)}
              >
                关闭
              </button>
            </div>
            <div className="min-h-0 flex-1 overflow-auto p-5">
              <div className="space-y-3">
                {applicationsTarget.applications.map((app) => {
                  const statusView = applicationStatusView(app.status);
                  return (
                    <div
                      className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-line p-4"
                      key={app.applicationId}
                    >
                      <div className="min-w-0">
                        <div className="truncate font-medium text-ink">
                          {app.jobTitle}
                        </div>
                        <div className="mt-1 text-xs text-muted">
                          {app.departmentName || "未设置部门"}
                        </div>
                        {!app.usesCurrentResume ? (
                          <div className="mt-1 text-xs text-amber-800">
                            当前申请仍采用历史简历版本
                          </div>
                        ) : null}
                      </div>
                      <div className="flex shrink-0 items-center gap-2">
                        <Badge className={statusView.tone}>
                          {statusView.label}
                        </Badge>
                        {app.primaryAction.type ===
                        "view_hard_screening_result" ? (
                          <Button
                            className="h-8 px-2.5 text-xs"
                            onClick={() => void openHardScreeningResult(app)}
                          >
                            {app.primaryAction.label}
                          </Button>
                        ) : (
                          <Link
                            to="/candidates"
                            state={workspaceEntryState(
                              location.pathname,
                              location.search,
                            )}
                          >
                            <Button
                              className="h-8 px-2.5 text-xs"
                              onClick={() => setApplicationsTarget(undefined)}
                            >
                              查看招聘流程
                            </Button>
                          </Link>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
            <div className="flex justify-end border-t border-line p-4">
              <Button onClick={() => setApplicationsTarget(undefined)}>
                关闭
              </Button>
            </div>
          </div>
        </div>
      ) : null}
      {hardScreeningTarget ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="candidate-hard-screening-title"
        >
          <div className="flex max-h-[86vh] w-full max-w-2xl flex-col overflow-hidden rounded-lg bg-white shadow-xl">
            <div className="flex items-start justify-between gap-4 border-b border-line p-5">
              <div>
                <h3
                  id="candidate-hard-screening-title"
                  className="font-semibold text-ink"
                >
                  硬筛结果
                </h3>
                <p className="mt-1 text-sm text-muted">
                  {hardScreeningTarget.jobTitle}
                  {hardScreeningTarget.departmentName
                    ? ` · ${hardScreeningTarget.departmentName}`
                    : ""}
                </p>
              </div>
              <button
                type="button"
                className="text-sm text-muted transition hover:text-ink"
                onClick={() => setHardScreeningTarget(undefined)}
              >
                关闭
              </button>
            </div>
            <div className="min-h-0 flex-1 overflow-auto p-5">
              {hardScreeningLoading ? (
                <p className="text-sm text-muted">正在加载硬筛结果...</p>
              ) : hardScreeningError ? (
                <div className="rounded-md bg-rose-50 p-3 text-sm text-rose-800">
                  {hardScreeningError}
                </div>
              ) : hardScreeningResult ? (
                <>
                  <p className="text-sm text-slate-700">
                    {hardScreeningResult.summary || "暂无汇总。"}
                  </p>
                  <div className="mt-3 space-y-2">
                    {hardScreeningResult.ruleResults.map((rule) => (
                      <div
                        key={rule.rule_id}
                        className="rounded-md border border-line p-3"
                      >
                        <div className="flex items-center justify-between gap-3">
                          <span className="font-semibold text-ink">
                            {rule.name}
                          </span>
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
                            <dt className="inline font-medium text-slate-700">
                              硬筛要求：
                            </dt>
                            <dd className="inline text-slate-900">
                              {rule.requirementText}
                            </dd>
                          </div>
                          <div>
                            <dt className="inline font-medium text-slate-700">
                              判断说明：
                            </dt>
                            <dd className="inline text-slate-800">
                              {rule.reason || "暂无自动判断说明。"}
                            </dd>
                          </div>
                        </dl>
                        <div className="mt-3 rounded bg-slate-50 p-2 text-xs leading-5 text-muted">
                          <div className="font-medium text-slate-700">
                            简历依据
                          </div>
                          {rule.source_quotes.length ? (
                            <div className="mt-1 space-y-1">
                              {rule.source_quotes.map((quote, index) => (
                                <p key={index}>“{quote}”</p>
                              ))}
                            </div>
                          ) : (
                            <p className="mt-1">
                              未找到可直接定位的简历原文，请人工核对候选人材料。
                            </p>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                </>
              ) : null}
            </div>
            <div className="flex justify-end border-t border-line p-4">
              <Button onClick={() => setHardScreeningTarget(undefined)}>
                关闭
              </Button>
            </div>
          </div>
        </div>
      ) : null}
      {ambiguousTarget ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="ambiguous-duplicate-title"
        >
          <div className="flex max-h-[86vh] w-full max-w-2xl flex-col overflow-hidden rounded-lg bg-white shadow-xl">
            <div className="border-b border-line p-5">
              <h3
                id="ambiguous-duplicate-title"
                className="font-semibold text-ink"
              >
                确认候选人归属
              </h3>
              <p className="mt-1 text-sm leading-6 text-muted">
                系统找到了多个可能为同一人的候选人。请选择确实对应的候选人；若都不是，可将本次简历作为新候选人继续处理。
              </p>
            </div>
            <div className="min-h-0 flex-1 overflow-auto p-5">
              {ambiguousLoading ? (
                <p className="text-sm text-muted">正在加载候选人匹配项…</p>
              ) : (
                <div className="space-y-3">
                  {ambiguousCandidates.map((candidate) => (
                    <label
                      key={candidate.candidateId}
                      className={`block rounded-lg border p-4 ${candidate.canReplace ? "cursor-pointer border-line hover:border-blue-300" : "cursor-not-allowed border-slate-200 bg-slate-50 text-slate-500"}`}
                    >
                      <div className="flex items-start gap-3">
                        <input
                          className="mt-1"
                          type="radio"
                          name="ambiguous-candidate"
                          disabled={!candidate.canReplace}
                          checked={
                            selectedCandidateId === candidate.candidateId
                          }
                          onChange={() =>
                            setSelectedCandidateId(candidate.candidateId)
                          }
                        />
                        <span className="min-w-0">
                          <span className="font-medium text-ink">
                            {candidate.displayName}
                          </span>
                          <span className="ml-2 text-xs text-muted">
                            {candidate.school || "学校未记录"} ·{" "}
                            {candidate.major || "专业未记录"}
                          </span>
                          <span className="mt-1 block text-xs text-muted">
                            关联申请 {candidate.applicationCount} 个
                            {candidate.activeApplicationCount
                              ? `，其中 ${candidate.activeApplicationCount} 个正在招聘流程中，不能覆盖`
                              : "，可采用新简历"}
                          </span>
                        </span>
                      </div>
                    </label>
                  ))}
                  {!ambiguousCandidates.length ? (
                    <p className="text-sm text-muted">
                      候选人匹配项已变化。你可以将本次简历作为新候选人继续处理。
                    </p>
                  ) : null}
                </div>
              )}
            </div>
            <div className="flex flex-wrap justify-end gap-2 border-t border-line p-4">
              <Button
                disabled={workingId === ambiguousTarget.submissionId}
                onClick={() => {
                  setAmbiguousTarget(undefined);
                  setAmbiguousCandidates([]);
                  setSelectedCandidateId("");
                }}
              >
                取消
              </Button>
              <Button
                disabled={
                  ambiguousLoading || workingId === ambiguousTarget.submissionId
                }
                onClick={() =>
                  void resolveAmbiguousDuplicateDecision("discard_submission")
                }
              >
                放弃本次导入
              </Button>
              <Button
                disabled={
                  ambiguousLoading || workingId === ambiguousTarget.submissionId
                }
                onClick={() =>
                  void resolveAmbiguousDuplicateDecision("create_new_candidate")
                }
              >
                均不是，创建新候选人
              </Button>
              <Button
                variant="primary"
                disabled={
                  ambiguousLoading ||
                  !selectedCandidateId ||
                  workingId === ambiguousTarget.submissionId
                }
                onClick={() =>
                  void resolveAmbiguousDuplicateDecision("merge_into_candidate")
                }
              >
                {workingId === ambiguousTarget.submissionId
                  ? "提交中"
                  : "确认采用所选候选人"}
              </Button>
            </div>
          </div>
        </div>
      ) : null}{" "}
      {duplicateAction ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="duplicate-decision-title"
        >
          <div className="w-full max-w-xl rounded-lg bg-white p-5 shadow-xl">
            <h3
              id="duplicate-decision-title"
              className="font-semibold text-ink"
            >
              {duplicateAction.decision === "replace_resume"
                ? "确认更新候选人简历"
                : "确认放弃本次导入"}
            </h3>
            <p className="mt-2 text-sm leading-6 text-muted">
              {duplicateAction.decision === "replace_resume"
                ? "系统识别到同一候选人。确认后会更新候选人的当前简历，并对仍适用的招聘评估重新计算，不会回退当前招聘阶段。"
                : "本次上传的简历将不被采用，已有候选人及其招聘申请不会受到影响。"}
            </p>
            {duplicateLoading ? (
              <p className="mt-4 text-sm text-muted">正在加载候选人影响范围…</p>
            ) : (
              <div className="mt-4 rounded-md border border-line bg-slate-50 p-3 text-sm text-slate-700">
                <p>
                  目标候选人：
                  {duplicateIntake?.duplicateTarget?.displayName ||
                    duplicateAction.item.candidateName}
                </p>
                <p className="mt-1">
                  关联岗位申请：
                  {duplicateIntake?.duplicateTarget?.applicationCount ?? 0} 个
                </p>
                {duplicateIntake?.duplicateTarget?.activeApplicationCount ? (
                  <p className="mt-1 text-xs text-muted">
                    其中 {duplicateIntake.duplicateTarget.activeApplicationCount}{" "}
                    个申请仍在招聘流程中
                  </p>
                ) : null}
              </div>
            )}
            <div className="mt-5 flex justify-end gap-2">
              <Button
                disabled={workingId === duplicateAction.item.submissionId}
                onClick={() => {
                  setDuplicateAction(undefined);
                  setDuplicateIntake(undefined);
                }}
              >
                取消
              </Button>
              <Button
                variant={
                  duplicateAction.decision === "replace_resume"
                    ? "primary"
                    : "secondary"
                }
                disabled={
                  duplicateLoading ||
                  (duplicateAction.decision === "replace_resume" &&
                    (!duplicateIntake?.duplicateTarget?.canReplace ||
                      !duplicateIntake.duplicateTarget.candidateId)) ||
                  workingId === duplicateAction.item.submissionId
                }
                onClick={() => void submitDuplicateDecision()}
              >
                {workingId === duplicateAction.item.submissionId
                  ? "提交中"
                  : duplicateAction.decision === "replace_resume"
                    ? "确认更新候选人简历"
                    : "确认放弃"}
              </Button>
            </div>
          </div>
        </div>
      ) : null}
      {routingTarget ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="manual-routing-title"
        >
          <div className="flex max-h-[86vh] w-full max-w-xl flex-col overflow-hidden rounded-lg bg-white shadow-xl">
            <div className="border-b border-line p-5">
              <h3 id="manual-routing-title" className="font-semibold text-ink">
                人工选择投递岗位
              </h3>
              <p className="mt-1 text-sm text-muted">
                {routingOptions?.candidateName || routingTarget.candidateName}
                {routingOptions?.candidateMajor
                  ? ` · ${routingOptions.candidateMajor}`
                  : ""}
              </p>
            </div>
            <div className="min-h-0 flex-1 overflow-auto p-5">
              {routingLoading ? (
                <p className="text-sm text-muted">正在加载可投递岗位…</p>
              ) : (
                <div className="space-y-2">
                  {routingOptions?.jobs.map((job) => {
                    const checked = selectedJobIds.includes(job.jobId);
                    return (
                      <label
                        key={job.jobId}
                        className="flex cursor-pointer items-start gap-3 rounded-md border border-line p-3 text-sm text-slate-700"
                      >
                        <input
                          className="mt-0.5"
                          type="checkbox"
                          checked={checked}
                          onChange={() =>
                            setSelectedJobIds((current) =>
                              checked
                                ? current.filter((id) => id !== job.jobId)
                                : [...current, job.jobId],
                            )
                          }
                        />
                        <span>
                          <span className="font-medium text-ink">
                            {job.title}
                          </span>
                          <span className="ml-2 text-xs text-muted">
                            {job.departmentName}
                          </span>
                          <span className="ml-2 text-xs text-slate-600">
                            {job.isScreeningReady
                              ? "画像已就绪，创建后进入初步筛选"
                              : "画像未就绪，创建后自动等待"}
                          </span>
                        </span>
                      </label>
                    );
                  })}
                  {!routingOptions?.jobs.length ? (
                    <p className="text-sm text-muted">
                      当前没有已确认的可投递岗位。
                    </p>
                  ) : null}
                </div>
              )}
            </div>
            <div className="flex justify-end gap-2 border-t border-line p-4">
              <Button
                disabled={workingId === routingTarget.submissionId}
                onClick={() => {
                  setRoutingTarget(undefined);
                  setRoutingOptions(undefined);
                  setSelectedJobIds([]);
                }}
              >
                取消
              </Button>
              <Button
                variant="primary"
                disabled={
                  routingLoading ||
                  selectedJobIds.length === 0 ||
                  workingId === routingTarget.submissionId
                }
                onClick={() => void submitManualRouting()}
              >
                {workingId === routingTarget.submissionId
                  ? "创建中"
                  : `确认创建 ${selectedJobIds.length} 个申请`}
              </Button>
            </div>
          </div>
        </div>
      ) : null}{" "}
      {correctionTarget ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="resume-correction-title"
        >
          <div className="flex max-h-[90vh] w-full max-w-6xl flex-col overflow-hidden rounded-lg bg-white shadow-xl">
            <div className="border-b border-line p-5">
              <h3
                id="resume-correction-title"
                className="font-semibold text-ink"
              >
                查看并校正结构化结果
              </h3>
              <p className="mt-1 text-sm text-muted">
                候选人：{correctionTarget.candidateName}
              </p>
            </div>
            <div className="min-h-0 flex-1 overflow-auto p-5">
              {" "}
              <details className="mb-5 rounded-md border border-line bg-slate-50">
                <summary className="cursor-pointer px-4 py-3 text-sm font-medium text-ink">
                  查看完整解析原文
                </summary>
                <div className="max-h-72 space-y-2 overflow-auto border-t border-line p-4">
                  {correctionLoading ? (
                    <p className="text-sm text-muted">正在加载解析结果…</p>
                  ) : (
                    <>
                      {correctionDraft?.sourceBlocks.map((block) => (
                        <div
                          key={block.blockId}
                          className="border-b border-line pb-2 text-sm last:border-b-0"
                        >
                          <span className="whitespace-pre-wrap leading-6 text-slate-700">
                            {block.text}
                          </span>
                        </div>
                      ))}
                      {!correctionDraft?.sourceBlocks.length ? (
                        <p className="text-sm text-muted">
                          没有可用于校正的解析块，请先强制重新解析。
                        </p>
                      ) : null}
                    </>
                  )}
                </div>
              </details>
              <div>
                <div className="mb-4">
                  <h4 className="text-sm font-semibold text-ink">结构化字段</h4>
                </div>
                {correctionLoading ? (
                  <p className="text-sm text-muted">正在加载结构化结果…</p>
                ) : (
                  <div className="space-y-4">
                  <div>
                    <h4 className="text-sm font-medium text-ink">教育经历</h4>
                    {(correctionEducations.length
                      ? correctionEducations
                      : [emptyCorrectionEducation]
                    ).map((education, index) => (
                      <div
                        key={`education-${index}`}
                        className="mt-3 border-t border-line pt-3 first:border-t-0 first:pt-0"
                      >
                        <div className="mb-2 flex items-center justify-between gap-2">
                          <span className="text-xs font-medium text-slate-600">
                            教育经历 {index + 1}
                          </span>
                          {correctionEducations.length > 1 ? (
                            <button
                              type="button"
                              className="grid size-8 place-items-center rounded-md text-rose-600 hover:bg-rose-50"
                              title="删除教育经历"
                              aria-label={`删除教育经历 ${index + 1}`}
                              onClick={() =>
                                setCorrectionEducations((current) =>
                                  current.filter(
                                    (_, itemIndex) => itemIndex !== index,
                                  ),
                                )
                              }
                            >
                              <Trash2 className="size-4" />
                            </button>
                          ) : null}
                        </div>
                        <div className="grid gap-3 sm:grid-cols-2">
                          <input
                            className="h-9 rounded-md border border-line px-3 text-sm"
                            value={education.school}
                            onChange={(event) =>
                              setCorrectionEducation(index, (value) => ({
                                ...value,
                                school: event.target.value,
                              }))
                            }
                            placeholder="学校"
                          />
                          <select
                            className="h-9 rounded-md border border-line px-3 text-sm"
                            value={education.degreeLevel}
                            onChange={(event) =>
                              setCorrectionEducation(index, (value) => ({
                                ...value,
                                degreeLevel: event.target.value,
                              }))
                            }
                          >
                            <option value="">学历层次</option>
                            <option value="associate">大专</option>
                            <option value="bachelor">本科</option>
                            <option value="master">硕士</option>
                            <option value="doctorate">博士</option>
                            <option value="other">其他</option>
                          </select>
                          <input
                            className="h-9 rounded-md border border-line px-3 text-sm"
                            value={education.major}
                            onChange={(event) =>
                              setCorrectionEducation(index, (value) => ({
                                ...value,
                                major: event.target.value,
                              }))
                            }
                            placeholder="专业"
                          />
                          <input
                            className="h-9 rounded-md border border-line px-3 text-sm"
                            value={education.startYear}
                            onChange={(event) =>
                              setCorrectionEducation(index, (value) => ({
                                ...value,
                                startYear: event.target.value,
                              }))
                            }
                            placeholder="入学年份"
                          />
                          <input
                            className="h-9 rounded-md border border-line px-3 text-sm"
                            value={education.graduationYear}
                            onChange={(event) =>
                              setCorrectionEducation(index, (value) => ({
                                ...value,
                                graduationYear: event.target.value,
                              }))
                            }
                            placeholder="毕业年份"
                          />
                          <select
                            className="h-9 rounded-md border border-line px-3 text-sm"
                            value={education.status}
                            onChange={(event) =>
                              setCorrectionEducation(index, (value) => ({
                                ...value,
                                status: event.target.value,
                              }))
                            }
                          >
                            <option value="unknown">状态待确认</option>
                            <option value="completed">已毕业</option>
                            <option value="in_progress">在读</option>
                          </select>
                        </div>
                        <SourceBlockPicker
                          blocks={correctionDraft?.sourceBlocks ?? []}
                          selected={education.sourceBlockIds}
                          onChange={(next) =>
                            setCorrectionEducation(index, (value) => ({
                              ...value,
                              sourceBlockIds: next,
                            }))
                          }
                          title="该教育经历来源（必选）"
                        />
                      </div>
                    ))}
                    <Button
                      className="mt-3 h-8 px-2.5 text-xs"
                      onClick={() =>
                        setCorrectionEducations((current) => [
                          ...current,
                          { ...emptyCorrectionEducation },
                        ])
                      }
                    >
                      新增教育经历
                    </Button>
                    <div className="mt-4 grid gap-3 sm:grid-cols-2">
                      <div>
                        <input
                          className="h-9 w-full rounded-md border border-line px-3 text-sm"
                          value={correctionExperienceYears.value}
                          onChange={(event) =>
                            setCorrectionExperienceYears((value) => ({
                              ...value,
                              value: event.target.value,
                            }))
                          }
                          placeholder="相关经验年限"
                        />
                        <SourceBlockPicker
                          blocks={correctionDraft?.sourceBlocks ?? []}
                          selected={correctionExperienceYears.sourceBlockIds}
                          onChange={(next) =>
                            setCorrectionExperienceYears((value) => ({
                              ...value,
                              sourceBlockIds: next,
                            }))
                          }
                          title="相关经验年限来源"
                        />
                      </div>
                    </div>
                  </div>
                  <div className="border-t border-line pt-4">
                    <h4 className="text-sm font-medium text-ink">
                      项目/工作经历（结构化结果）
                    </h4>
                    <div className="mt-3">
                      <input
                        className="h-9 rounded-md border border-line px-3 text-sm"
                        value={correctionProject.title}
                        onChange={(event) =>
                          setCorrectionProject((value) => ({
                            ...value,
                            title: event.target.value,
                          }))
                        }
                        placeholder="项目标题"
                      />
                    </div>
                    <StructuredProjectPreview project={correctionProject} />
                    <SourceBlockPicker
                      blocks={correctionDraft?.sourceBlocks ?? []}
                      selected={correctionProject.titleBlockIds}
                      onChange={(next) =>
                        setCorrectionProject((value) => ({
                          ...value,
                          titleBlockIds: next,
                        }))
                      }
                      title="标题原文（必选）"
                    />
                    <SourceBlockPicker
                      blocks={correctionDraft?.sourceBlocks ?? []}
                      selected={correctionProject.contextBlockIds}
                      onChange={(next) =>
                        setCorrectionProject((value) => ({
                          ...value,
                          contextBlockIds: next,
                        }))
                      }
                      title="项目上下文（可选）"
                    />
                    <SourceBlockPicker
                      blocks={correctionDraft?.sourceBlocks ?? []}
                      selected={correctionProject.workEvidenceBlockIds}
                      onChange={(next) =>
                        setCorrectionProject((value) => ({
                          ...value,
                          workEvidenceBlockIds: next,
                        }))
                      }
                      title="直接工作证据（至少一条）"
                    />
                    {correctionProjects.slice(1).map((project, index) => {
                      const projectIndex = index + 1;
                      return (
                        <div
                          key={`project-${projectIndex}`}
                          className="mt-4 rounded-md border border-line p-3"
                        >
                          <div className="mb-2 flex items-center justify-between gap-2">
                            <span className="text-xs font-medium text-slate-600">
                              项目 {projectIndex + 1}
                            </span>
                            <button
                              type="button"
                              className="text-xs text-rose-600 hover:text-rose-800"
                              onClick={() =>
                                setCorrectionProjects((current) =>
                                  current.filter(
                                    (_, itemIndex) =>
                                      itemIndex !== projectIndex,
                                  ),
                                )
                              }
                            >
                              删除
                            </button>
                          </div>
                          <div>
                            <input
                              className="h-9 rounded-md border border-line px-3 text-sm"
                              value={project.title}
                              onChange={(event) =>
                                setCorrectionProjects((current) =>
                                  current.map((value, itemIndex) =>
                                    itemIndex === projectIndex
                                      ? { ...value, title: event.target.value }
                                      : value,
                                  ),
                                )
                              }
                              placeholder="项目标题"
                            />
                          </div>
                          <StructuredProjectPreview project={project} />
                          <SourceBlockPicker
                            blocks={correctionDraft?.sourceBlocks ?? []}
                            selected={project.titleBlockIds}
                            onChange={(next) =>
                              setCorrectionProjects((current) =>
                                current.map((value, itemIndex) =>
                                  itemIndex === projectIndex
                                    ? { ...value, titleBlockIds: next }
                                    : value,
                                ),
                              )
                            }
                            title="标题原文（必选）"
                          />
                          <SourceBlockPicker
                            blocks={correctionDraft?.sourceBlocks ?? []}
                            selected={project.contextBlockIds}
                            onChange={(next) =>
                              setCorrectionProjects((current) =>
                                current.map((value, itemIndex) =>
                                  itemIndex === projectIndex
                                    ? { ...value, contextBlockIds: next }
                                    : value,
                                ),
                              )
                            }
                            title="项目上下文（可选）"
                          />
                          <SourceBlockPicker
                            blocks={correctionDraft?.sourceBlocks ?? []}
                            selected={project.workEvidenceBlockIds}
                            onChange={(next) =>
                              setCorrectionProjects((current) =>
                                current.map((value, itemIndex) =>
                                  itemIndex === projectIndex
                                    ? { ...value, workEvidenceBlockIds: next }
                                    : value,
                                ),
                              )
                            }
                            title="直接工作证据（至少一条）"
                          />
                        </div>
                      );
                    })}
                    <Button
                      className="mt-3 h-8 px-2.5 text-xs"
                      onClick={() =>
                        setCorrectionProjects((current) => [
                          ...current,
                          {
                            experienceUnitId: "",
                            title: "",
                            titleBlockIds: [],
                            contextBlockIds: [],
                            workEvidenceBlockIds: [],
                            contextItems: [],
                            sourceBullets: [],
                          },
                        ])
                      }
                    >
                      新增项目
                    </Button>
                  </div>
                  <div className="border-t border-line pt-4">
                    <h4 className="text-sm font-medium text-ink">技能声明</h4>
                    <div className="mt-3 grid gap-3 sm:grid-cols-2">
                      <input
                        className="h-9 rounded-md border border-line px-3 text-sm"
                        value={correctionSkill.name}
                        onChange={(event) =>
                          setCorrectionSkill((value) => ({
                            ...value,
                            name: event.target.value,
                          }))
                        }
                        placeholder="技能主题"
                      />
                      <input
                        className="h-9 rounded-md border border-line px-3 text-sm"
                        value={correctionSkill.details}
                        onChange={(event) =>
                          setCorrectionSkill((value) => ({
                            ...value,
                            details: event.target.value,
                          }))
                        }
                        placeholder="原文中的技能细节"
                      />
                    </div>
                    <SourceBlockPicker
                      blocks={correctionDraft?.sourceBlocks ?? []}
                      selected={correctionSkill.sourceBlockIds}
                      onChange={(next) =>
                        setCorrectionSkill((value) => ({
                          ...value,
                          sourceBlockIds: next,
                        }))
                      }
                      title="技能来源原文（必选）"
                    />
                    {correctionSkills.slice(1).map((skill, index) => {
                      const skillIndex = index + 1;
                      return (
                        <div
                          key={`skill-${skillIndex}`}
                          className="mt-4 rounded-md border border-line p-3"
                        >
                          <div className="mb-2 flex items-center justify-between gap-2">
                            <span className="text-xs font-medium text-slate-600">
                              技能 {skillIndex + 1}
                            </span>
                            <button
                              type="button"
                              className="text-xs text-rose-600 hover:text-rose-800"
                              onClick={() =>
                                setCorrectionSkills((current) =>
                                  current.filter(
                                    (_, itemIndex) => itemIndex !== skillIndex,
                                  ),
                                )
                              }
                            >
                              删除
                            </button>
                          </div>
                          <div className="grid gap-2 sm:grid-cols-2">
                            <input
                              className="h-9 rounded-md border border-line px-3 text-sm"
                              value={skill.name}
                              onChange={(event) =>
                                setCorrectionSkills((current) =>
                                  current.map((value, itemIndex) =>
                                    itemIndex === skillIndex
                                      ? { ...value, name: event.target.value }
                                      : value,
                                  ),
                                )
                              }
                              placeholder="技能主题"
                            />
                            <input
                              className="h-9 rounded-md border border-line px-3 text-sm"
                              value={skill.details}
                              onChange={(event) =>
                                setCorrectionSkills((current) =>
                                  current.map((value, itemIndex) =>
                                    itemIndex === skillIndex
                                      ? {
                                          ...value,
                                          details: event.target.value,
                                        }
                                      : value,
                                  ),
                                )
                              }
                              placeholder="原文中的技能细节"
                            />
                          </div>
                          <SourceBlockPicker
                            blocks={correctionDraft?.sourceBlocks ?? []}
                            selected={skill.sourceBlockIds}
                            onChange={(next) =>
                              setCorrectionSkills((current) =>
                                current.map((value, itemIndex) =>
                                  itemIndex === skillIndex
                                    ? { ...value, sourceBlockIds: next }
                                    : value,
                                ),
                              )
                            }
                            title="技能来源原文（必选）"
                          />
                        </div>
                      );
                    })}
                    <Button
                      className="mt-3 h-8 px-2.5 text-xs"
                      onClick={() =>
                        setCorrectionSkills((current) => [
                          ...current,
                          { name: "", details: "", sourceBlockIds: [] },
                        ])
                      }
                    >
                      新增技能
                    </Button>
                  </div>
                  </div>
                )}
              </div>
            </div>
            <div className="flex justify-end gap-2 border-t border-line p-4">
              <Button
                disabled={workingId === correctionTarget.submissionId}
                onClick={() => {
                  setCorrectionTarget(undefined);
                  setCorrectionDraft(undefined);
                }}
              >
                取消
              </Button>
              <Button
                variant="primary"
                disabled={
                  correctionLoading ||
                  workingId === correctionTarget.submissionId
                }
                onClick={() => void submitCorrection()}
              >
                {workingId === correctionTarget.submissionId
                  ? "提交中"
                  : "提交校正"}
              </Button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
