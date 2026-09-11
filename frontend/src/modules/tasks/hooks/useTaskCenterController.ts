import { useCallback, useEffect, useRef, useState } from "react";
import { useAuth } from "@/modules/auth/AuthProvider";
import { getActiveJobs, type JobOption } from "@/modules/documents/resumeApi";
import { getMyTasks, type TaskListView } from "@/modules/tasks/api";
import {
  announceNavigationNotificationChange,
  markNavigationNotificationRead
} from "@/modules/tasks/notificationsApi";

const emptyView: TaskListView = {
  items: [],
  total: 0,
  overdueCount: 0,
  page: 1,
  pageSize: 30
};

export function useTaskCenterController() {
  const { token, user } = useAuth();
  const [view, setView] = useState<TaskListView>(emptyView);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [keyword, setKeyword] = useState("");
  const [debouncedKeyword, setDebouncedKeyword] = useState("");
  const [jobs, setJobs] = useState<JobOption[]>([]);
  const [jobId, setJobId] = useState("");
  const [sort, setSort] = useState<"priority" | "score_desc" | "score_asc">("score_desc");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(30);
  const taskReadMarked = useRef(false);

  const loadTasks = useCallback(async (silent = false) => {
    if (!token) return;
    if (!silent) setLoading(true);
    setError("");
    try {
      const nextView = await getMyTasks(token, {
        page,
        pageSize,
        keyword: debouncedKeyword,
        jobId: jobId || undefined,
        sortBy: sort.startsWith("score") ? "currentScore" : "priority",
        sortOrder: sort === "score_asc" ? "asc" : "desc"
      });
      setView(nextView);
      if (!silent && !taskReadMarked.current) {
        taskReadMarked.current = true;
        void markNavigationNotificationRead(token, "task")
          .then(announceNavigationNotificationChange)
          .catch(() => {
            taskReadMarked.current = false;
          });
      }
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "任务列表加载失败");
    } finally {
      if (!silent) setLoading(false);
    }
  }, [debouncedKeyword, jobId, page, pageSize, sort, token]);

  useEffect(() => {
    void loadTasks();
  }, [loadTasks]);

  useEffect(() => {
    if (!token) return;
    void getActiveJobs(token).then(setJobs).catch(() => setJobs([]));
  }, [token]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setDebouncedKeyword(keyword.trim());
      setPage(1);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [keyword]);

  return {
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
  };
}
