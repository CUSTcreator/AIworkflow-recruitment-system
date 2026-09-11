import type { WorkflowExecutionEvent } from "./process";
import { formatWorkflowTime } from "./time";

/**
 * 复用在候选人和申请详情的简版运行轨迹。
 *
 * 此处故意只展示业务安全事件：处理步骤、可读文案和错误原因中文标签；管理员排障时
 * 才到系统管理页查看完整技术日志，避免把外部回包或原始异常带入业务页面。
 */
export function WorkflowExecutionTimeline({
  events,
  loading,
  error,
}: {
  events: WorkflowExecutionEvent[];
  loading?: boolean;
  error?: string;
}) {
  if (loading && events.length === 0) {
    return <p className="text-sm text-muted">正在加载执行轨迹…</p>;
  }
  if (error) {
    return <p className="rounded-md bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</p>;
  }
  if (events.length === 0) {
    return <p className="text-sm text-muted">该任务暂未产生可展示的执行事件。</p>;
  }
  return <ol className="space-y-3 border-l border-slate-200 pl-4">
    {events.map((event) => (
      <li className="relative" key={event.executionEventId}>
        <span className={`absolute -left-[21px] top-1.5 h-2.5 w-2.5 rounded-full ${
          event.severity === "error" ? "bg-rose-500" : event.severity === "warning" ? "bg-amber-500" : "bg-blue-500"
        }`} />
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
          <span className="font-medium text-ink">{event.stepLabel || "流程更新"}</span>
          <span className="text-xs text-muted">{formatWorkflowTime(event.occurredAt)}</span>
        </div>
        <p className="mt-1 text-sm leading-5 text-slate-700">{event.message}</p>
        <div className="mt-1 flex flex-wrap gap-x-3 text-xs text-muted">
          {event.attemptCount ? <span>第 {event.attemptCount} 次尝试</span> : null}
          {event.pollCount ? <span>第 {event.pollCount} 次轮询</span> : null}
          {event.nextAttemptAt ? <span>下次尝试：{formatWorkflowTime(event.nextAttemptAt)}</span> : null}
          {event.errorCodeLabel ? <span>{event.errorCodeLabel}</span> : null}
          {event.recoveryActionLabel ? <span>{event.recoveryActionLabel}</span> : null}
        </div>
      </li>
    ))}
  </ol>;
}


