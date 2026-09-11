import { RefreshCw, RotateCcw, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { retryJobDocument } from "@/modules/jobs/documentApi";
import {
  getImportRecords,
  markImportRecordsRead,
  type ImportRecord,
  type ImportRecordType
} from "@/modules/jobs/api";
import {
  createApplicationsFromIntake,
  decideResumeDuplicate,
  getCandidateIntake,
  getManualRoutingOptions,
  rebuildCandidateFromResume,
  retryCandidateResumePublish,
  retryCandidateRouting,
  type CandidateIntake,
  type ManualRoutingJob
} from "@/modules/documents/resumeApi";
import { hasRecoveryAction, recoveryAction } from "@/shared/recovery/actions";
import { useAuth } from "@/modules/auth/AuthProvider";
import { toUserFacingError } from "@/shared/utils/displayText";
import { Button } from "@/shared/ui/Button";
import { DocumentStatusBadge } from "./DocumentStatusBadge";
import { WorkflowProcessStatus } from "@/shared/ui/WorkflowProcessStatus";
import { isWorkflowProcessActive } from "@/shared/workflows/process";

export function ImportRecordDrawer({
  open,
  type,
  onClose,
  onReviewJobImport,
  onRead
}: {
  open: boolean;
  type?: ImportRecordType;
  onClose: () => void;
  onReviewJobImport?: (documentId: string) => void;
  onRead?: () => void;
}) {
  const { token } = useAuth();
  const navigate = useNavigate();
  const [records, setRecords] = useState<ImportRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [retryingId, setRetryingId] = useState("");
  const [error, setError] = useState("");
  const [routingRecord, setRoutingRecord] = useState<ImportRecord>();
  const [intake, setIntake] = useState<CandidateIntake>();
  const [jobs, setJobs] = useState<ManualRoutingJob[]>([]);
  const [selectedJobIds, setSelectedJobIds] = useState<string[]>([]);
  const [routingSubmitting, setRoutingSubmitting] = useState(false);

  const load = useCallback(async () => {
    if (!token || !open) return;
    setLoading(true);
    setError("");
    try {
      setRecords((await getImportRecords(token, type)).items);
      if (type) {
        await markImportRecordsRead(token, type);
        onRead?.();
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "导入记录加载失败");
    } finally {
      setLoading(false);
    }
  }, [open, token, type]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!records.some((record) => isWorkflowProcessActive(record.process))) return;
    const timer = window.setInterval(() => void load(), 3000);
    return () => window.clearInterval(timer);
  }, [load, records]);

  if (!open) return null;

  async function retry(record: ImportRecord, action?: string) {
    if (!token) return;
    setRetryingId(record.importId);
    setError("");
    try {
      if (record.importType === "job") {
        await retryJobDocument(token, record.importId);
      } else {
        if (action === "retry_publish_resume") {
          await retryCandidateResumePublish(token, record.importId);
        } else if (action === "retry_routing") {
          await retryCandidateRouting(token, record.importId);
        } else {
          await rebuildCandidateFromResume(token, record.importId);
        }
      }
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "重新处理失败");
    } finally {
      setRetryingId("");
    }
  }

  async function openManualRouting(record: ImportRecord) {
    if (!token) return;
    setError("");
    setRoutingRecord(record);
    try {
      const [nextIntake, nextJobs] = await Promise.all([
        getCandidateIntake(token, record.importId),
        getManualRoutingOptions(token, record.importId)
      ]);
      setIntake(nextIntake);
      setJobs(nextJobs.jobs);
      setSelectedJobIds([]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "岗位分发信息加载失败");
    }
  }

  async function decideDuplicate(record: ImportRecord, decision: "replace_resume" | "discard_submission") {
    if (!token) return;
    // 导入记录只保留摘要信息；执行覆盖前重新读取候选人聚合，避免多候选人命中时误覆盖。
    let currentIntake: CandidateIntake;
    try {
      currentIntake = await getCandidateIntake(token, record.importId);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "重复候选人信息加载失败");
      return;
    }
    if (hasRecoveryAction(currentIntake.availableActions, "resolve_duplicate_candidates")) {
      setError("该简历可能属于多名候选人。请前往“简历处理”页面确认候选人归属后再继续处理。");
      return;
    }
    const confirmed = decision === "replace_resume"
      ? window.confirm("将以本次简历替换该候选人此前未推进的初步筛选记录，并重新进入初步筛选。已通过、已淘汰等历史结论不会被改动。是否继续？")
      : window.confirm("放弃本次重复简历导入？");
    if (!confirmed) return;
    setRoutingSubmitting(true);
    setError("");
    try {
      await decideResumeDuplicate(token, record.importId, decision);
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "重复简历处理失败");
    } finally {
      setRoutingSubmitting(false);
    }
  }
  async function submitManualRouting() {
    if (!token || !routingRecord || selectedJobIds.length === 0) return;
    setRoutingSubmitting(true);
    setError("");
    try {
      await createApplicationsFromIntake(token, routingRecord.importId, selectedJobIds);
      setRoutingRecord(undefined);
      setIntake(undefined);
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "创建岗位申请失败");
    } finally {
      setRoutingSubmitting(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 bg-slate-950/30" role="dialog" aria-modal="true">
      <aside className="absolute bottom-0 right-0 top-0 flex w-full max-w-2xl flex-col bg-white shadow-2xl">
        <header className="flex items-center justify-between border-b border-line px-5 py-4">
          <div>
            <h2 className="text-lg font-semibold text-ink">导入记录</h2>
            <p className="mt-1 text-xs text-muted">只展示HR需要处理的导入状态与异常。</p>
          </div>
          <div className="flex items-center gap-2">
            <Button type="button" onClick={() => void load()} disabled={loading}>
              <RefreshCw size={15} />刷新
            </Button>
            <button className="rounded-md p-2 text-muted hover:bg-slate-100" onClick={onClose} aria-label="关闭导入记录">
              <X size={18} />
            </button>
          </div>
        </header>
        <div className="app-scrollbar flex-1 overflow-y-auto p-5">
          {error ? <p className="mb-3 rounded-md bg-rose-50 p-3 text-sm text-rose-700">{error}</p> : null}
          {loading && records.length === 0 ? <p className="text-sm text-muted">正在加载……</p> : null}
          <div className="space-y-3">
            {records.map((record) => (
              <article key={`${record.importType}-${record.importId}`} className="rounded-md border border-line p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="truncate font-medium text-ink">{record.displayName}</div>
                    <div className="mt-1 text-xs text-muted">
                      {record.importType === "job" ? "招聘要求" : "候选简历"} · {formatTime(record.createdAt)}
                    </div>
                  </div>
                  <div className="space-y-1 text-right">
                    <DocumentStatusBadge status={record.status} />
                    <WorkflowProcessStatus process={record.process} compact />
                  </div>
                </div>
                <p className="mt-3 text-sm text-slate-700">{record.resultSummary}</p>
                {record.errorMessage ? (
                  <p className="mt-2 rounded-md bg-rose-50 px-3 py-2 text-xs leading-5 text-rose-700">
                    {toUserFacingError(record.errorMessage)}
                  </p>
                ) : null}
                <div className="mt-3 flex justify-end gap-2">
                  {record.importType === "job" && (hasRecoveryAction(record.availableActions, "review_job_import") || hasRecoveryAction(record.availableActions, "review_job_drafts") || hasRecoveryAction(record.availableActions, "review_job_fields") || hasRecoveryAction(record.availableActions, "review_job_headers")) && onReviewJobImport ? (
                    <Button type="button" variant="primary" onClick={() => onReviewJobImport(record.importId)}>
                      {(recoveryAction(record.availableActions, "review_job_import") || recoveryAction(record.availableActions, "review_job_drafts") || recoveryAction(record.availableActions, "review_job_fields") || recoveryAction(record.availableActions, "review_job_headers"))?.label}
                    </Button>
                  ) : null}
                  {hasRecoveryAction(record.availableActions, "retry_job_import") ||
                  hasRecoveryAction(record.availableActions, "retry_job_extraction") ||
                  hasRecoveryAction(record.availableActions, "retry_publish_job_drafts") ||
                  hasRecoveryAction(record.availableActions, "force_fresh_parse") ||
                  hasRecoveryAction(record.availableActions, "retry_publish_resume") ||
                  hasRecoveryAction(record.availableActions, "retry_routing") ? (
                    <Button
                      type="button"
                      disabled={retryingId === record.importId}
                      onClick={() => {
                        const action = record.importType === "resume"
                          ? (hasRecoveryAction(record.availableActions, "retry_publish_resume")
                            ? "retry_publish_resume"
                            : hasRecoveryAction(record.availableActions, "retry_routing")
                              ? "retry_routing"
                              : "force_fresh_parse")
                          : undefined;
                        void retry(record, action);
                      }}
                    >
                      <RotateCcw size={15} />
                      {retryingId === record.importId
                        ? "提交中"
                        : recoveryAction(
                            record.availableActions,
                            record.importType === "job"
                              ? (hasRecoveryAction(record.availableActions, "retry_job_extraction") ? "retry_job_extraction" : hasRecoveryAction(record.availableActions, "retry_publish_job_drafts") ? "retry_publish_job_drafts" : "retry_job_import")
                              : (hasRecoveryAction(record.availableActions, "retry_publish_resume")
                                ? "retry_publish_resume"
                                : hasRecoveryAction(record.availableActions, "retry_routing")
                                  ? "retry_routing"
                                  : "force_fresh_parse"),
                          )?.label}
                    </Button>
                  ) : null}
                  {record.importType === "resume" && (
                    hasRecoveryAction(record.availableActions, "replace_duplicate_resume") ||
                    hasRecoveryAction(record.availableActions, "discard_submission")
                  ) ? (
                    <div className="text-right">
                      <div className="mb-2 text-xs text-muted">若系统提示多个候选人，请到“简历处理”页确认归属。</div>
                      <div className="flex justify-end gap-2">
                        {hasRecoveryAction(record.availableActions, "discard_submission") ? (
                          <Button type="button" disabled={routingSubmitting} onClick={() => void decideDuplicate(record, "discard_submission")}>{recoveryAction(record.availableActions, "discard_submission")?.label}</Button>
                        ) : null}
                        {hasRecoveryAction(record.availableActions, "replace_duplicate_resume") ? (
                          <Button type="button" variant="primary" disabled={routingSubmitting} onClick={() => void decideDuplicate(record, "replace_resume")}>{recoveryAction(record.availableActions, "replace_duplicate_resume")?.label}</Button>
                        ) : null}
                      </div>
                    </div>
                  ) : null}
                  {record.importType === "resume" && hasRecoveryAction(record.availableActions, "create_applications") ? (
                    <Button type="button" variant="primary" onClick={() => void openManualRouting(record)}>
                      {recoveryAction(record.availableActions, "create_applications")?.label}
                    </Button>
                  ) : null}
                  {record.importType === "resume" && [
                    "correct_parsed_resume",
                    "upload_replacement_resume",
                    "resolve_duplicate_candidates",
                    "retry_publish_resume",
                    "retry_routing",
                  ].some((action) => hasRecoveryAction(record.availableActions, action)) ? (
                    <Button
                      type="button"
                      onClick={() => {
                        onClose();
                        navigate(`/candidates?view=intakes&submissionId=${encodeURIComponent(record.importId)}`);
                      }}
                    >
                      前往简历处理
                    </Button>
                  ) : null}
                </div>
              </article>
            ))}
          </div>
          {routingRecord ? (
            <section className="mt-5 rounded-md border border-blue-200 bg-blue-50/50 p-4">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h3 className="font-semibold text-ink">人工选择投递岗位</h3>
                  <p className="mt-1 text-xs leading-5 text-muted">{intake?.candidate?.display_name || routingRecord.displayName} · 专业：{intake?.candidate?.major || "未识别"}</p>
                </div>
                <button className="text-xs text-muted hover:text-ink" type="button" onClick={() => setRoutingRecord(undefined)}>取消</button>
              </div>
              <div className="mt-3 max-h-56 space-y-2 overflow-y-auto">
                {jobs.map((job) => {
                  const checked = selectedJobIds.includes(job.jobId);
                  return <label key={job.jobId} className="flex cursor-pointer items-start gap-2 rounded-md bg-white px-3 py-2 text-sm text-slate-700">
                    <input type="checkbox" className="mt-0.5" checked={checked} onChange={() => setSelectedJobIds((current) => checked ? current.filter((id) => id !== job.jobId) : [...current, job.jobId])} />
                    <span><span className="font-medium text-ink">{job.title}</span><span className="ml-2 text-xs text-muted">{job.departmentName}</span></span>
                  </label>;
                })}
                {jobs.length === 0 ? <p className="text-sm text-muted">当前没有可投递岗位。</p> : null}
              </div>
              <div className="mt-4 flex justify-end">
                <Button type="button" variant="primary" disabled={selectedJobIds.length === 0 || routingSubmitting} onClick={() => void submitManualRouting()}>
                  {routingSubmitting ? "创建中" : `确认创建 ${selectedJobIds.length || ""} 个申请`}
                </Button>
              </div>
            </section>
          ) : null}
          {!loading && records.length === 0 ? <p className="text-sm text-muted">暂无导入记录。</p> : null}
        </div>
      </aside>
    </div>
  );
}

function formatTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}
