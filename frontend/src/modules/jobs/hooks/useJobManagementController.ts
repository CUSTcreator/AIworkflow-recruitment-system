import { useCallback, useEffect, useMemo, useState } from "react";
import { useAuth } from "@/modules/auth/AuthProvider";
import {
  deleteManagedJob, getImportRecords, getManagedJobs, requestJobProfileRun, updateManagedJob,
  type JobProfileRunResult, type JobUpdateInput, type ManagedJob
} from "@/modules/jobs/api";
import { usePeriodicRefresh } from "@/shared/hooks/usePeriodicRefresh";
import { hasBusinessPermission } from "@/modules/auth/permissions";

export function useJobManagementController() {
  const { token, user } = useAuth();
  const [jobs, setJobs] = useState<ManagedJob[]>([]);
  const [keyword, setKeyword] = useState("");
  const [status, setStatus] = useState("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selectedJob, setSelectedJob] = useState<ManagedJob>();
  const [importOpen, setImportOpen] = useState(false);
  const [recordsOpen, setRecordsOpen] = useState(false);
  const [reviewDocumentId, setReviewDocumentId] = useState("");
  const [jobImportUnreadCount, setJobImportUnreadCount] = useState(0);
  const [deletingJobId, setDeletingJobId] = useState("");
  const [profileEditJobId, setProfileEditJobId] = useState("");
  const [profileActionJobId, setProfileActionJobId] = useState("");

  const loadJobs = useCallback(async (silent = false) => {
    if (!token) return;
    if (!silent) setLoading(true);
    setError("");
    try {
      const next = await getManagedJobs(token);
      setJobs(next);
      setSelectedJob((current) => current ? next.find((job) => job.jobId === current.jobId) : undefined);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "岗位列表加载失败");
    } finally {
      if (!silent) setLoading(false);
    }
  }, [token]);

  useEffect(() => { void loadJobs(); }, [loadJobs]);

  // 仅在画像任务活跃时轮询，避免岗位列表无意义地持续请求。
  const hasActiveProfileRun = jobs.some((job) => ["queued", "processing"].includes(job.profileStatus));
  usePeriodicRefresh(() => loadJobs(true), hasActiveProfileRun);

  const loadJobImportUnreadCount = useCallback(async () => {
    if (!token || !hasBusinessPermission(user, "job_document.upload")) return;
    try { setJobImportUnreadCount((await getImportRecords(token, "job")).unreadActionCount); }
    catch { setJobImportUnreadCount(0); }
  }, [token, user]);
  useEffect(() => { void loadJobImportUnreadCount(); }, [loadJobImportUnreadCount]);

  const filtered = useMemo(() => jobs.filter((job) => {
    const searchText = `${job.title}${job.departmentName}${job.jobId}${job.sourceFilename ?? ""}`.toLowerCase();
    return (!keyword.trim() || searchText.includes(keyword.trim().toLowerCase()))
      && (status === "all" || job.status === status);
  }), [jobs, keyword, status]);

  const reviewImport = useCallback((documentId: string) => {
    setReviewDocumentId(documentId); setRecordsOpen(false); setImportOpen(true);
  }, []);

  const deleteJob = useCallback(async (job: ManagedJob) => {
    if (!token || deletingJobId) return;
    setDeletingJobId(job.jobId); setError("");
    try {
      await deleteManagedJob(token, job.jobId);
      setSelectedJob(undefined);
      await loadJobs();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "删除岗位失败");
    } finally { setDeletingJobId(""); }
  }, [deletingJobId, loadJobs, token]);

  const updateJob = useCallback(async (job: ManagedJob, input: JobUpdateInput) => {
    if (!token) return undefined;
    setError("");
    try {
      const updated = await updateManagedJob(token, job.jobId, input);
      setJobs((current) => current.map((item) => item.jobId === updated.jobId ? updated : item));
      setSelectedJob(updated);
      return updated;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "保存岗位失败");
      return undefined;
    }
  }, [token]);

  const runProfileAction = useCallback(async (
    job: ManagedJob, action: JobProfileRunResult["action"],
  ) => {
    if (!token || profileActionJobId) return undefined;
    setProfileActionJobId(job.jobId); setError("");
    try {
      const result = await requestJobProfileRun(token, job.jobId, action);
      await loadJobs(true);
      return result;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "提交岗位画像任务失败");
      return undefined;
    } finally { setProfileActionJobId(""); }
  }, [loadJobs, profileActionJobId, token]);

  return {
    token, user, jobs, filtered, keyword, setKeyword, status, setStatus, loading, error,
    selectedJob, setSelectedJob, importOpen, setImportOpen, recordsOpen, setRecordsOpen,
    reviewDocumentId, setReviewDocumentId, jobImportUnreadCount, setJobImportUnreadCount,
    deletingJobId, profileEditJobId, setProfileEditJobId, profileActionJobId, loadJobs, loadJobImportUnreadCount, reviewImport,
    updateJob, deleteJob, runProfileAction
  };
}
