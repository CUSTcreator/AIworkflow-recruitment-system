import { LoaderCircle } from "lucide-react";

import type { WorkflowProcess } from "@/shared/workflows/process";
import { formatWorkflowTime } from "@/shared/workflows/time";
import { Badge } from "@/shared/ui/Badge";

const presentation = {
  queued: { label: "等待执行", tone: "border-slate-200 bg-slate-50 text-slate-700" },
  running: { label: "处理中", tone: "border-blue-200 bg-blue-50 text-blue-700" },
  retry_wait: { label: "自动重试中", tone: "border-amber-200 bg-amber-50 text-amber-800" },
  waiting_external: { label: "等待外部结果", tone: "border-amber-200 bg-amber-50 text-amber-800" },
  blocked: { label: "待确认", tone: "border-amber-200 bg-amber-50 text-amber-800" },
  failed: { label: "处理失败", tone: "border-rose-200 bg-rose-50 text-rose-700" },
  completed: { label: "已完成", tone: "border-emerald-200 bg-emerald-50 text-emerald-700" },
  cancelled: { label: "已取消", tone: "border-slate-200 bg-slate-50 text-slate-600" },
  not_started: { label: "未开始", tone: "border-slate-200 bg-slate-50 text-slate-600" }
} as const;

const recoveryHint = {
  auto_retry: "系统会自动恢复，无需重复提交。",
  user_retry: "修复相关信息后可使用“重试”。",
  review_required: "请完成确认或补充信息后继续。",
  continue_manually: "可改用人工方式继续当前招聘环节。",
} as const;

export function WorkflowProcessStatus({ process, compact = false }: { process?: WorkflowProcess | null; compact?: boolean }) {
  // 紧凑模式用于业务主状态下方的辅助提示。流程已完成时，主状态本身已经表达结果，
  // 不再重复显示“已完成”；处理中、失败、阻塞等需要用户关注的信息仍照常展示。
  if (!process || (compact && process.processStatus === "completed")) return null;
  const item = presentation[process.processStatus];
  const active = process.processStatus === "queued" || process.processStatus === "running";
  return (
    <div className={compact ? "space-y-1" : "rounded-md border border-line bg-slate-50 p-3"}>
      <div className="flex flex-wrap items-center gap-2">
        <Badge className={item.tone}>{active ? <LoaderCircle className="mr-1 animate-spin" size={12} /> : null}{item.label}</Badge>
        {process.currentStepLabel ? <span className="text-xs font-medium text-slate-700">{process.currentStepLabel}</span> : null}
      </div>
      {process.publicMessage ? <p className="text-xs leading-5 text-muted">{process.publicMessage}</p> : null}
      {process.nextAttemptAt ? <p className="text-xs text-muted">下次尝试：{formatWorkflowTime(process.nextAttemptAt)}</p> : null}
      {process.recoveryAction && recoveryHint[process.recoveryAction] ? <p className="text-xs text-muted">{recoveryHint[process.recoveryAction]}</p> : null}
    </div>
  );
}
