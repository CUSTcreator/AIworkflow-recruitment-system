import { useState } from "react";
import type {
  AssessmentCoverage,
  DecisionFocusCollection,
  StageHandoff
} from "@/modules/assessment/contracts";
import { ChevronDown } from "lucide-react";
import { toUserFacingText } from "@/shared/utils/displayText";
import { Badge } from "@/shared/ui/Badge";

const priorityLabel = { high: "优先确认", medium: "需要确认", low: "一般关注" } as const;
const priorityTone = {
  high: "border-rose-200 bg-rose-50 text-rose-700",
  medium: "border-amber-200 bg-amber-50 text-amber-800",
  low: "border-slate-200 bg-slate-50 text-slate-600"
} as const;

export function DecisionFocusCard({
  focus,
  onEvidenceClick
}: {
  focus: DecisionFocusCollection;
  onEvidenceClick?: (evidenceId: string) => void;
}) {
  const [showAll, setShowAll] = useState(false);
  const visibleItems = focus.items.slice(0, showAll ? 5 : 3);
  const hiddenCount = Math.max(0, Math.min(focus.items.length, 5) - 3);
  const statusLabel: Record<string, string> = {
    open: "待确认",
    unresolved: "待确认",
    partially_verified: "部分确认",
    contradicted: "存在冲突",
    verified: "已确认",
    resolved: "已确认"
  };

  return (
    <section className="rounded-md border border-line bg-white p-4">
      <div>
        <h2 className="text-sm font-semibold text-ink">本轮决策重点</h2>
        <p className="mt-1 text-xs text-muted">优先核实最影响推进判断的问题</p>
      </div>

      <div className="mt-4 space-y-2">
        {visibleItems.map((item, index) => (
          <details key={item.focusId} className="group rounded-md border border-line bg-white open:border-blue-200 open:bg-blue-50/30">
            <summary className="flex cursor-pointer list-none items-start gap-2.5 px-3 py-3">
              <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-slate-100 text-[11px] font-semibold text-slate-600 group-open:bg-blue-600 group-open:text-white">
                {index + 1}
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-start justify-between gap-2">
                  <span className="text-sm font-semibold leading-5 text-ink">{toUserFacingText(item.title)}</span>
                  <div className="flex shrink-0 items-center gap-2">
                    <Badge className={priorityTone[item.priority]}>{priorityLabel[item.priority]}</Badge>
                    <ChevronDown size={14} className="text-muted transition group-open:rotate-180" />
                  </div>
                </div>
                {item.oneLineReason ? (
                  <p className="mt-1 line-clamp-2 text-xs leading-5 text-muted group-open:line-clamp-none">
                    {toUserFacingText(item.oneLineReason)}
                  </p>
                ) : null}
              </div>
            </summary>

            <div className="ml-10 border-t border-blue-100 px-3 py-3">
              {statusLabel[item.status] ? (
                <div className="flex flex-wrap items-center gap-2">
                  <span className={`rounded-full px-2 py-0.5 text-[11px] ${
                    item.status === "contradicted" ? "bg-rose-100 text-rose-700" : "bg-slate-100 text-slate-600"
                  }`}>{statusLabel[item.status]}</span>
                </div>
              ) : null}
              <DetailRow label="当前判断" value={item.currentConclusion} />
              {item.missingInformation ? <DetailRow label="仍缺信息" value={item.missingInformation} /> : null}
              {item.verificationAction ? <DetailRow label="核实方式" value={item.verificationAction} /> : null}
              {item.sourceRefs.length > 0 ? (
                <div className="mt-3">
                  <div className="text-[11px] font-medium text-muted">相关依据</div>
                  <div className="mt-1.5 flex flex-wrap gap-1.5">
                    {item.sourceRefs.map((evidenceId, evidenceIndex) => (
                      <button
                        key={evidenceId}
                        type="button"
                        disabled={!onEvidenceClick}
                        onClick={() => onEvidenceClick?.(evidenceId)}
                        className="rounded-full border border-blue-200 bg-white px-2.5 py-1 text-[11px] text-blue-700 enabled:hover:bg-blue-50 disabled:cursor-default disabled:text-slate-500"
                      >
                        依据 {evidenceIndex + 1}
                      </button>
                    ))}
                  </div>
                </div>
              ) : null}
            </div>
          </details>
        ))}

        {focus.items.length === 0 ? (
          <p className="rounded-md bg-slate-50 px-3 py-4 text-xs text-muted">当前没有需要补充核实的事项。</p>
        ) : null}

        {hiddenCount > 0 ? (
          <button
            type="button"
            className="flex w-full items-center justify-center gap-1 rounded-md py-2 text-xs font-semibold text-blue-700 hover:bg-blue-50 hover:text-blue-900"
            onClick={() => setShowAll((value) => !value)}
          >
            {showAll ? "收起" : `展开其余 ${hiddenCount} 项`}
            <ChevronDown size={14} className={`transition ${showAll ? "rotate-180" : ""}`} />
          </button>
        ) : null}
      </div>
    </section>
  );
}

function DetailRow({ label, value }: { label: string; value: string }) {
  if (!value) return null;
  return (
    <div className="mt-3">
      <div className="text-[11px] font-medium text-muted">{label}</div>
      <p className="mt-1 text-xs leading-5 text-slate-700">{toUserFacingText(value)}</p>
    </div>
  );
}
export function AssessmentCoverageCard({ coverage }: { coverage: AssessmentCoverage }) {
  const percent = Math.round(coverage.coverageRate * 100);
  return (
    <section className="rounded-md border border-line p-4">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-sm font-semibold text-ink">本轮评估覆盖</h2>
        <strong className="text-sm text-ink">{percent}%</strong>
      </div>
      <div className="mt-3 h-2 overflow-hidden rounded-full bg-slate-100">
        <div className="h-full bg-blue-500" style={{ width: `${percent}%` }} />
      </div>
      <p className="mt-3 text-xs text-muted">
        已充分评估 {coverage.fullyAssessed} 项，部分评估 {coverage.partiallyAssessed} 项，未评估 {coverage.notAssessed} 项。
      </p>
    </section>
  );
}

export function StageHandoffCard({ handoff }: { handoff?: StageHandoff | null }) {
  if (!handoff) return null;
  const strengths = handoff.confirmedStrengths.filter((item) => item.trim());
  const remaining = handoff.remainingItems.filter((item) => item.trim());
  const conclusion = handoff.decisionReason || handoff.recommendation;
  if (!conclusion && strengths.length === 0 && remaining.length === 0) return null;
  return (
    <section className="rounded-md border border-line p-4">
      <h2 className="mb-3 text-sm font-semibold text-ink">上一阶段结论</h2>
      {conclusion ? <p className="text-sm leading-6 text-slate-700">{toUserFacingText(conclusion)}</p> : null}
      {strengths.length > 0 ? (
        <p className="mt-3 text-xs leading-5 text-emerald-700">已确认优势：{strengths.map(toUserFacingText).join("；")}</p>
      ) : null}
      {remaining.length > 0 ? (
        <p className="mt-2 text-xs leading-5 text-amber-800">仍需确认：{remaining.map(toUserFacingText).join("；")}</p>
      ) : null}
    </section>
  );
}
