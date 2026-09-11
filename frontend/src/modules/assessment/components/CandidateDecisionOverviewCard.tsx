import { ChevronDown, Eye } from "lucide-react";
import { useState } from "react";
import type {
  AssessmentChangeSet,
  AssessmentScoreChange,
  CandidateDecisionOverviewView,
  DecisionSummaryItemView,
  VerificationFocusItemView
} from "@/modules/assessment/contracts";
import { ScoreExplanationButton } from "@/modules/assessment/components/ScoreExplanationButton";
import { toUserFacingText } from "@/shared/utils/displayText";

type OverviewStage = "screening" | "after_first_interview" | "after_second_interview";

const aiTitle: Record<OverviewStage, string> = {
  screening: "AI 初步筛选摘要",
  after_first_interview: "AI 一面后摘要",
  after_second_interview: "AI 二面后摘要"
};

const focusTitle: Record<OverviewStage, string> = {
  screening: "一面核验重点",
  after_first_interview: "二面关注重点",
  after_second_interview: "最终决策关注项"
};

const recommendationLabel: Record<OverviewStage, Record<string, string>> = {
  screening: {
    strongly_recommend: "强烈建议通过初步筛选",
    recommend: "建议通过初步筛选",
    cautious_recommend: "建议通过初步筛选，需重点核验",
    not_recommend: "建议不通过初步筛选",
    strongly_not_recommend: "明确不通过初步筛选"
  },
  after_first_interview: {
    strongly_recommend: "强烈建议进入二面",
    recommend: "建议进入二面",
    cautious_recommend: "建议进入二面，需重点核验",
    not_recommend: "建议不进入二面",
    strongly_not_recommend: "明确不进入二面"
  },
  after_second_interview: {
    strongly_recommend: "强烈建议录用",
    recommend: "建议录用",
    cautious_recommend: "建议录用，需重点核验",
    not_recommend: "建议不录用",
    strongly_not_recommend: "明确不录用"
  }
};

const recommendationTone: Record<string, string> = {
  strongly_recommend: "text-emerald-700",
  recommend: "text-emerald-600",
  cautious_recommend: "text-amber-700",
  not_recommend: "text-rose-600",
  strongly_not_recommend: "text-rose-700"
};

const focusTypeLabel: Record<string, string> = {
  experience_verification: "经历核验",
  experience_supplement: "经历补充",
  direct_demonstration: "现场考察",
  live_assessment: "现场考察"
};

function scoreText(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(1) : "待核验";
}

function scoreChangeText(
  change: AssessmentScoreChange | undefined,
  showUnchangedFallback: boolean
): string | null {
  if (typeof change?.delta === "number" && Number.isFinite(change.delta)) {
    const delta = Math.abs(change.delta) < 0.05 ? 0 : change.delta;
    return `较上一版 ${delta >= 0 ? "+" : ""}${delta.toFixed(1)}`;
  }
  // V2/V3 变化集合只记录有业务变化的字段；缺少字段代表本轮没有该项变化，
  // 但审核人员仍需要看到明确的“+0”，不能因为 DTO 省略字段而产生歧义。
  return showUnchangedFallback ? "较上一版 +0.0" : null;
}

function signalStatusLabel(
  status: "added" | "retained" | "closed" | undefined,
  showUnchangedFallback: boolean
): string | null {
  return status === "added" ? "新增" : status === "retained" ? "保持" : status === "closed" ? "已关闭" : showUnchangedFallback ? "保持" : null;
}

/** 将已关闭的历史信号补入当前摘要，保证 V2/V3 展示的是前后版本并集。 */
function mergeSummaryItems(items: DecisionSummaryItemView[], changes: AssessmentChangeSet["strengths"]): DecisionSummaryItemView[] {
  const knownKeys = new Set(items.flatMap((item) => item.sourceSignalIds));
  const knownTitles = new Set(items.map((item) => item.title));
  return [...items, ...changes
    .filter((item) => !knownKeys.has(item.signalKey) && !knownTitles.has(item.title))
    .map((item) => ({ title: item.title, summary: item.summary, sourceSignalIds: [item.signalKey], evidenceIds: item.evidenceIds }))];
}

/** 将已经关闭的核验目标补入当前列表，页面才能解释本轮为什么不再继续追问。 */
function mergeFocusItems(items: VerificationFocusItemView[], changes: AssessmentChangeSet["verificationFocus"]): VerificationFocusItemView[] {
  const knownIds = new Set(items.flatMap((item) => item.sourceInterviewTargetIds));
  const knownTitles = new Set(items.map((item) => item.title));
  return [...items, ...changes
    .filter((item) => !knownIds.has(item.targetId) && !knownTitles.has(item.title))
    .map((item) => ({
      focusId: item.targetId,
      focusType: "experience_verification",
      title: item.title,
      reason: item.reason,
      verificationGoal: item.goal,
      sourceInterviewTargetIds: [item.targetId],
      status: item.status,
      evidenceIds: item.evidenceIds
    }))];
}

export function CandidateDecisionOverviewCard({
  overview,
  stage,
  pendingMessage,
  changes,
  showScoreChanges: showScoreChangesOverride,
  onEvidenceClick
}: {
  overview: CandidateDecisionOverviewView;
  /** 必须传后端 assessmentStage，不能按当前页面推测。 */
  stage: OverviewStage;
  /** 所在工作台期待更晚版本但尚未发布时，明确说明当前展示的版本。 */
  pendingMessage?: string;
  /** V2/V3 已发布 AAV 相对前一版的变化；仅用于展示，不参与计算。 */
  changes?: AssessmentChangeSet | null;
  /** V2/V3 页面可显式打开变化展示，缺少字段时由卡片显示 +0.0。 */
  showScoreChanges?: boolean;
  onEvidenceClick?: (evidenceId: string) => void;
}) {
  const [summaryExpanded, setSummaryExpanded] = useState(false);
  const [focusExpanded, setFocusExpanded] = useState(false);
  const { assessment, aiSummary, verificationFocus } = overview;
  const allStrengths = mergeSummaryItems(aiSummary.strengths, changes?.strengths ?? []);
  const allWeaknesses = mergeSummaryItems(aiSummary.weaknesses, changes?.weaknesses ?? []);
  const allFocus = mergeFocusItems(verificationFocus.items, changes?.verificationFocus ?? []);
  const strengths = summaryExpanded ? allStrengths : allStrengths.slice(0, 2);
  const weaknesses = summaryExpanded ? allWeaknesses : allWeaknesses.slice(0, 2);
  const hasHiddenSummary = allStrengths.length > 2 || allWeaknesses.length > 2;
  const visibleFocus = focusExpanded ? allFocus : allFocus.slice(0, 3);
  const hiddenFocusCount = Math.max(0, allFocus.length - 3);
  const recommendationLevel = aiSummary.recommendationLevel;
  const scoreChanges = new Map((changes?.scoreChanges ?? []).map((item) => [item.metric, item]));
  const showScoreChanges = showScoreChangesOverride ?? stage !== "screening";
  const showChangeLabels = showScoreChanges;
  const signalChanges = new Map([
    ...(changes?.strengths ?? []),
    ...(changes?.weaknesses ?? [])
  ].map((item) => [item.signalKey, item.changeStatus]));
  const targetChanges = new Map((changes?.verificationFocus ?? []).map((item) => [item.targetId, item.status]));

  return (
    <section className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-[0_1px_3px_rgba(15,23,42,0.06)]">
      <div className="px-5 py-5">
        {pendingMessage ? (
          <p className="mb-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-800">
            {pendingMessage}
          </p>
        ) : null}
        <SectionHeading index="Ⅰ" title="综合评估" />
        <div className="mt-4 flex items-end gap-4">
          <div className="flex items-end gap-2">
            <span className="text-[42px] font-semibold leading-none tracking-tight text-slate-950">
              {scoreText(assessment.totalScore)}
            </span>
            <div className="mb-0.5 flex flex-col items-start gap-1 text-xs text-slate-500">
              <span className="flex items-center gap-1">综合能力分
              <ScoreExplanationButton
                stage={stage}
                total={assessment.totalScore}
                jobFit={assessment.jobFitScore}
                resumeExperience={assessment.experienceScore}
                education={assessment.educationScore}
              /></span>
              {scoreChangeText(scoreChanges.get("total"), showScoreChanges) ? <span>{scoreChangeText(scoreChanges.get("total"), showScoreChanges)}</span> : null}
            </div>
          </div>
        </div>
        <div className="mt-5 grid grid-cols-3 divide-x divide-slate-200 rounded-md bg-slate-50 py-3">
          <Metric label="岗位匹配" value={assessment.jobFitScore} change={scoreChanges.get("job_fit")} showChange={showScoreChanges} />
          <Metric label="经历能力" value={assessment.experienceScore} change={scoreChanges.get("experience")} showChange={showScoreChanges} />
          <Metric label="学历背景" value={assessment.educationScore} change={scoreChanges.get("education")} showChange={showScoreChanges} />
        </div>
      </div>

      <div className="border-t border-slate-200 px-5 py-5">
        <div className="flex items-center justify-between gap-3">
          <SectionHeading index="Ⅱ" title={aiTitle[stage]} />
          <GenerationModeHint mode={aiSummary.generationMode} />
        </div>
        <div className="mt-4 border-l-2 border-blue-600 pl-3">
          <div className={`text-sm font-semibold ${recommendationTone[recommendationLevel] ?? recommendationTone.cautious_recommend}`}>
            {recommendationLabel[stage][recommendationLevel] ?? recommendationLabel[stage].cautious_recommend}
          </div>
          {aiSummary.recommendationReason ? (
            <p className="mt-1 text-xs leading-5 text-slate-600">
              {toUserFacingText(aiSummary.recommendationReason)}
            </p>
          ) : null}
        </div>
        {changes?.roundChangeSummary ? (
          <div className="mt-4 rounded-md border border-violet-200 bg-violet-50 px-3 py-2">
            <div className="text-xs font-semibold text-violet-950">{changes.roundChangeSummary.title}</div>
            {changes.roundChangeSummary.summary ? <p className="mt-1 text-xs leading-5 text-violet-900">{toUserFacingText(changes.roundChangeSummary.summary)}</p> : null}
          </div>
        ) : null}

        <div className="mt-5 grid grid-cols-2 divide-x divide-slate-200">
          <SummaryGroup
            title="优势表现"
            items={strengths}
            emptyText="暂无明确优势"
            markerClass="bg-emerald-500"
            numberTone="text-emerald-600"
            signalChanges={signalChanges}
            showChangeLabels={showChangeLabels}
            onEvidenceClick={onEvidenceClick}
          />
          <SummaryGroup
            title="薄弱项"
            items={weaknesses}
            emptyText="暂无明确薄弱项"
            markerClass="bg-amber-500"
            numberTone="text-amber-600"
            signalChanges={signalChanges}
            showChangeLabels={showChangeLabels}
            onEvidenceClick={onEvidenceClick}
          />
        </div>

        {hasHiddenSummary ? (
          <button
            type="button"
            className="mt-4 flex w-full items-center justify-center gap-1 border-t border-slate-100 pt-3 text-xs font-medium text-slate-500 hover:text-blue-700"
            onClick={() => setSummaryExpanded((value) => !value)}
          >
            {summaryExpanded ? "收起分析" : "查看完整分析"}
            <ChevronDown size={14} className={`transition ${summaryExpanded ? "rotate-180" : ""}`} />
          </button>
        ) : null}
      </div>

      <div className="border-t border-slate-200 px-5 py-5">
        <div className="flex items-center justify-between gap-3">
          <SectionHeading index="Ⅲ" title={focusTitle[stage]} />
          <GenerationModeHint mode={verificationFocus.generationMode} />
        </div>
        <div className="mt-4 divide-y divide-slate-100 border-y border-slate-100">
          {visibleFocus.map((item, index) => (
            <FocusItem
              key={item.focusId || `${item.title}-${index}`}
              item={item}
              index={index}
              targetStatus={item.sourceInterviewTargetIds.map((id) => targetChanges.get(id)).find(Boolean) ?? (showChangeLabels ? item.status : undefined)}
              onEvidenceClick={onEvidenceClick}
            />
          ))}
          {allFocus.length === 0 ? (
            <p className="py-4 text-xs text-slate-500">当前没有需要补充核实的事项。</p>
          ) : null}
        </div>
        {hiddenFocusCount > 0 ? (
          <button
            type="button"
            className="mt-3 flex w-full items-center justify-center gap-1 text-xs font-medium text-slate-500 hover:text-blue-700"
            onClick={() => setFocusExpanded((value) => !value)}
          >
            {focusExpanded ? "收起" : `展开其余 ${hiddenFocusCount} 项`}
            <ChevronDown size={14} className={`transition ${focusExpanded ? "rotate-180" : ""}`} />
          </button>
        ) : null}
      </div>
    </section>
  );
}

function GenerationModeHint({ mode }: { mode: "llm" | "rule_fallback" }) {
  const fallback = mode === "rule_fallback";
  return (
    <span className={`shrink-0 rounded-full border px-2 py-0.5 text-[11px] font-medium ${fallback
      ? "border-amber-200 bg-amber-50 text-amber-800"
      : "border-blue-200 bg-blue-50 text-blue-700"}`}>
      {fallback ? "规则模板生成" : "AI 文案生成"}
    </span>
  );
}
function SectionHeading({ index, title }: { index: string; title: string }) {
  return (
    <div className="flex items-baseline gap-3">
        <span className="min-w-5 font-serif text-[15px] font-semibold leading-none text-blue-600">{index}</span>
        <h2 className="text-sm font-semibold text-slate-900">{title}</h2>
    </div>
  );
}

function Metric({ label, value, change, showChange }: { label: string; value: number | null; change?: AssessmentScoreChange; showChange: boolean }) {
  const changeText = scoreChangeText(change, showChange);
  return (
    <div className="px-3 text-center first:pl-0 last:pr-0">
      <div className="text-lg font-semibold tabular-nums text-slate-900">{scoreText(value)}</div>
      <div className="mt-0.5 text-[11px] text-slate-500">{label}</div>
      {changeText ? <div className="mt-0.5 text-[10px] text-slate-500">{changeText}</div> : null}
    </div>
  );
}

function SummaryGroup({
  title,
  items,
  emptyText,
  markerClass,
  numberTone,
  signalChanges,
  showChangeLabels,
  onEvidenceClick
}: {
  title: string;
  items: DecisionSummaryItemView[];
  emptyText: string;
  markerClass: string;
  onEvidenceClick?: (evidenceId: string) => void;
  numberTone: string;
  signalChanges: Map<string, "added" | "retained" | "closed" | undefined>;
  showChangeLabels: boolean;
}) {
  return (
    <div className="px-3 first:pl-0 last:pr-0">
      <div className="flex items-center gap-2 text-xs font-semibold text-slate-700">
        <span className={`h-1.5 w-1.5 rounded-full ${markerClass}`} />
        {title}
      </div>
      {items.length === 0 ? (
        <p className="mt-3 text-xs text-slate-400">{emptyText}</p>
      ) : (
        <div className="mt-3 space-y-3">
          {items.map((item, index) => (
            <div key={`${item.title}-${index}`} className="flex items-start gap-2">
              <span className={`mt-0.5 w-5 shrink-0 font-mono text-xs font-semibold tabular-nums ${numberTone}`}>
                {String(index + 1).padStart(2, "0")}
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2 text-xs font-medium leading-5 text-slate-800">
                  <span>{toUserFacingText(item.title)}</span>
                  {signalStatusLabel(item.sourceSignalIds.map((id) => signalChanges.get(id)).find(Boolean), showChangeLabels) ? (
                    <span className="rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5 text-[10px] font-medium text-slate-600">
                      {signalStatusLabel(item.sourceSignalIds.map((id) => signalChanges.get(id)).find(Boolean), showChangeLabels)}
                    </span>
                  ) : null}
                </div>
                {item.summary.trim() && item.summary.trim() !== item.title.trim() ? (
                  <div className="mt-0.5 text-[11px] leading-5 text-slate-500">{toUserFacingText(item.summary)}</div>
                ) : null}
                <EvidenceLink evidenceIds={item.evidenceIds} onEvidenceClick={onEvidenceClick} />
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function FocusItem({
  item,
  index,
  targetStatus,
  onEvidenceClick
}: {
  item: VerificationFocusItemView;
  index: number;
  targetStatus?: "open" | "resolved";
  onEvidenceClick?: (evidenceId: string) => void;
}) {
  return (
    <details className="group">
      <summary className="flex cursor-pointer list-none items-start gap-3 py-3.5">
        <span className="mt-0.5 w-8 shrink-0 font-mono text-[13px] font-semibold text-slate-400 group-open:text-blue-600">
          #{String(index + 1).padStart(2, "0")}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <div className="text-[11px] font-medium text-slate-400">{focusTypeLabel[item.focusType] ?? "核验重点"}</div>
              <div className="mt-0.5 flex flex-wrap items-center gap-2 text-sm font-semibold leading-5 text-slate-900">
                <span>{toUserFacingText(item.title)}</span>
                {targetStatus ? <span className={`rounded border px-1.5 py-0.5 text-[10px] font-medium ${targetStatus === "open" ? "border-amber-200 bg-amber-50 text-amber-800" : "border-emerald-200 bg-emerald-50 text-emerald-700"}`}>{targetStatus === "open" ? "仍待确认" : "已关闭"}</span> : null}
              </div>
            </div>
            <ChevronDown size={14} className="mt-0.5 shrink-0 text-slate-400 transition group-open:rotate-180" />
          </div>
        </div>
      </summary>
      <div className="mb-3 ml-8 border-l-2 border-slate-200 pl-3">
        <Detail label="为什么关注" value={item.reason} />
        <Detail label="本轮需要确认" value={item.verificationGoal} />
        {item.evidenceIds.length > 0 ? (
          <div className="mt-3">
            <div className="text-[11px] font-medium text-slate-400">相关依据</div>
            <div className="mt-1.5 flex flex-wrap gap-3">
              {item.evidenceIds.map((evidenceId, evidenceIndex) => (
                <button
                  key={`${evidenceId}-${evidenceIndex}`}
                  type="button"
                  disabled={!onEvidenceClick}
                  onClick={() => onEvidenceClick?.(evidenceId)}
                  className="text-[11px] font-medium text-blue-700 enabled:hover:underline disabled:cursor-default disabled:text-slate-400"
                >
                  依据 {evidenceIndex + 1}
                </button>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </details>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  if (!value) return null;
  return (
    <div className="mt-3 first:mt-0">
      <div className="text-[11px] font-medium text-slate-400">{label}</div>
      <p className="mt-1 text-xs leading-5 text-slate-600">{toUserFacingText(value)}</p>
    </div>
  );
}

function EvidenceLink({
  evidenceIds,
  onEvidenceClick
}: {
  evidenceIds: string[];
  onEvidenceClick?: (evidenceId: string) => void;
}) {
  const evidenceId = evidenceIds[0];
  if (!evidenceId || !onEvidenceClick) return null;
  return (
    <button
      type="button"
      className="mt-1 inline-flex items-center gap-0.5 text-[11px] font-medium text-blue-700 hover:underline"
      onClick={() => onEvidenceClick(evidenceId)}
    >
      <Eye size={12} /> 查看依据
    </button>
  );
}
