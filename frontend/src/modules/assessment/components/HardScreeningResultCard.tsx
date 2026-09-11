import { useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  Clock3,
  Eye,
  ShieldCheck,
  XCircle,
} from "lucide-react";

import type {
  HardScreeningRequirementStatus,
  HardScreeningReviewView,
} from "@/modules/assessment/contracts";
import type { Job } from "@/modules/applications/contracts";

const overallLabels: Record<HardScreeningReviewView["status"], string> = {
  not_configured: "未配置",
  pending: "等待核验",
  running: "核验中",
  passed: "全部通过",
  failed: "未通过",
  manual_review: "需要核验",
};

const requirementLabels: Record<HardScreeningRequirementStatus, string> = {
  passed: "通过",
  failed: "未通过",
  manual_review: "待核验",
  not_evaluated: "未完成",
};

function RequirementStatus({ status }: { status: HardScreeningRequirementStatus }) {
  const icon =
    status === "passed" ? (
      <CheckCircle2 size={15} aria-hidden="true" />
    ) : status === "failed" ? (
      <XCircle size={15} aria-hidden="true" />
    ) : status === "manual_review" ? (
      <AlertTriangle size={15} aria-hidden="true" />
    ) : (
      <Clock3 size={15} aria-hidden="true" />
    );
  const color =
    status === "passed"
      ? "text-emerald-700"
      : status === "failed"
        ? "text-rose-700"
        : status === "manual_review"
          ? "text-amber-700"
          : "text-slate-500";
  return (
    <span className={`flex shrink-0 items-center gap-1 text-xs font-semibold ${color}`}>
      {icon}
      {requirementLabels[status]}
    </span>
  );
}

function requirementSummary(value: HardScreeningReviewView): string {
  const { counts } = value;
  if (counts.total === 0) return value.summary || "该岗位未配置硬筛要求。";
  if (counts.failed > 0 || counts.manualReview > 0 || counts.notEvaluated > 0) {
    const parts = [
      counts.passed > 0 ? `${counts.passed} 项通过` : "",
      counts.failed > 0 ? `${counts.failed} 项未通过` : "",
      counts.manualReview > 0 ? `${counts.manualReview} 项待核验` : "",
      counts.notEvaluated > 0 ? `${counts.notEvaluated} 项未完成` : "",
    ].filter(Boolean);
    return parts.join(" · ");
  }
  return `${counts.total} 项硬性条件均已满足`;
}

/**
 * 岗位字段保持由外层工作台 DTO 提供；本卡片的 value 仅表示本次 V1 冻结的硬筛结果。
 * 默认不展开已通过规则，避免 V1/V2/V3 工作台的侧栏被重复的核验细节占满。
 */
export function HardScreeningResultCard({
  job,
  value,
}: {
  job: Pick<Job, "title" | "department" | "educationRequirement" | "majorRequirement">;
  value: HardScreeningReviewView;
}) {
  const [showAll, setShowAll] = useState(false);
  const hasExceptions =
    value.counts.failed > 0 ||
    value.counts.manualReview > 0 ||
    value.counts.notEvaluated > 0;
  const exceptionRequirements = value.requirements.filter(
    (item) => item.status !== "passed",
  );
  const visibleRequirements = showAll
    ? value.requirements
    : exceptionRequirements;
  const jobRequirements = [job.educationRequirement, job.majorRequirement].filter(
    (item): item is string => Boolean(item?.trim()),
  );

  return (
    <section className="overflow-hidden rounded-md border border-line bg-white">
      <div className="px-4 py-3">
        <div className="flex items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-2 text-sm font-semibold text-ink">
            <ShieldCheck size={16} className="shrink-0 text-slate-500" aria-hidden="true" />
            <span>岗位硬筛</span>
          </div>
          <span className="shrink-0 text-xs font-semibold text-slate-600">
            {overallLabels[value.status]}
          </span>
        </div>
        <p className="mt-1 truncate text-sm font-medium text-ink">{job.title}</p>
        {job.department ? (
          <p className="mt-0.5 text-xs text-muted">{job.department}</p>
        ) : null}
        {jobRequirements.length > 0 ? (
          <p className="mt-2 line-clamp-1 text-xs leading-5 text-slate-600">
            {jobRequirements.join(" · ")}
          </p>
        ) : null}
      </div>

      <div className="border-t border-line px-4 py-2.5">
        <p className="text-xs font-medium text-slate-700">{requirementSummary(value)}</p>
      </div>

      {value.requirements.length > 0 && (showAll || hasExceptions) ? (
        <div className="divide-y divide-line border-t border-line">
          {visibleRequirements.map((item) => (
            <div key={item.ruleId} className="px-4 py-2.5">
              <div className="flex items-start justify-between gap-3">
                <p className="min-w-0 text-xs leading-5 text-ink">
                  <span className="font-medium">{item.name}</span>
                  <span className="text-slate-600">：{item.requirementText}</span>
                </p>
                <RequirementStatus status={item.status} />
              </div>
              {item.reason || item.sourceQuotes.length > 0 ? (
                <details className="group mt-1.5">
                  <summary className="flex cursor-pointer list-none items-center gap-1 text-xs font-medium text-blue-700 hover:text-blue-900">
                    <Eye size={13} aria-hidden="true" />
                    查看判断依据
                    <ChevronDown
                      size={13}
                      className="transition group-open:rotate-180"
                      aria-hidden="true"
                    />
                  </summary>
                  <div className="mt-2 space-y-1.5 border-l-2 border-slate-200 pl-3">
                    {item.reason ? (
                      <p className="text-xs leading-5 text-muted">{item.reason}</p>
                    ) : null}
                    {item.sourceQuotes.map((quote, index) => (
                      <p key={`${item.ruleId}-${index}`} className="break-words text-xs leading-5 text-slate-600">
                        {quote}
                      </p>
                    ))}
                  </div>
                </details>
              ) : null}
            </div>
          ))}
        </div>
      ) : null}

      {value.requirements.length > 0 ? (
        <button
          type="button"
          className="flex w-full items-center justify-center gap-1 border-t border-line px-4 py-2 text-xs font-semibold text-blue-700 hover:bg-slate-50 hover:text-blue-900"
          onClick={() => setShowAll((current) => !current)}
          aria-expanded={showAll}
        >
          {showAll ? "收起详细规则" : `查看全部 ${value.counts.total} 项`}
          <ChevronDown
            size={14}
            className={`transition ${showAll ? "rotate-180" : ""}`}
            aria-hidden="true"
          />
        </button>
      ) : null}
    </section>
  );
}
