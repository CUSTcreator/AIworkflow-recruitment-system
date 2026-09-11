import { ChevronDown } from "lucide-react";
import type { ReactNode } from "react";

import type { JobCapabilityView, JobRequirementView } from "../contracts";
import { Badge } from "@/shared/ui/Badge";

export interface CapabilityUpdateView {
  label: string;
  tone: string;
  detail?: ReactNode;
}

export function JobCapabilityMatchPanel({
  requirements,
  evidenceButton,
  getUpdate,
  getScoreUpdate
}: {
  requirements: JobRequirementView[];
  evidenceButton: (ids: string[]) => ReactNode;
  getUpdate?: (targetIds: string[]) => CapabilityUpdateView | undefined;
  /** V2/V3 已发布的能力分变化；与面评证据提示并列展示。 */
  getScoreUpdate?: (targetIds: string[]) => CapabilityUpdateView | undefined;
}) {
  const items = capabilityItems(requirements);
  if (items.length === 0) {
    return <p className="text-sm text-muted">当前没有可展示的岗位能力要求。</p>;
  }

  const coreItems = items.filter((item) => item.capability.role === "core");
  const sortedItems = sortByMatchStrength(items);
  const supportedItems = sortedItems.filter(hasResumeSupport);
  const unsupportedItems = sortedItems.filter((item) => !hasResumeSupport(item));
  const visibleItems = supportedItems.slice(0, 4);
  const remainingItems = [...supportedItems.slice(4), ...unsupportedItems];
  const fullySupported = coreItems.filter((item) => (item.capability.score ?? 0) >= 65).length;
  const partlySupported = coreItems.filter((item) => (item.capability.score ?? 0) > 0 && (item.capability.score ?? 0) < 65).length;
  const unsupported = coreItems.filter((item) => (item.capability.score ?? 0) <= 0).length;
  const strongest = supportedItems.slice(0, 3);
  const gaps = [...coreItems]
    .filter((item) => (item.capability.score ?? 0) > 0 && (item.capability.score ?? 0) < 65)
    .sort((left, right) => (right.capability.score ?? 0) - (left.capability.score ?? 0))
    .slice(0, 2);
  const remainingAreAllUnsupported = remainingItems.length > 0
    && remainingItems.every((item) => !hasResumeSupport(item));

  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-blue-100 bg-blue-50/60 p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="text-sm font-semibold text-slate-800">岗位能力证明情况</div>
          <div className="text-xs text-slate-600">
            充分 {fullySupported} · 部分 {partlySupported} · 缺少证明 {unsupported}
          </div>
        </div>
        <div className="mt-4 grid gap-4">
          <CapabilityTags title="较强匹配" items={strongest} emptyText="暂无明确强项" />
          <CapabilityTags title="需要进一步验证" items={gaps} emptyText="暂无明显缺口" warning />
        </div>
      </div>

      {visibleItems.length > 0 ? (
        <CapabilitySection
          items={visibleItems}
          evidenceButton={evidenceButton}
          getUpdate={getUpdate}
          getScoreUpdate={getScoreUpdate}
        />
      ) : null}

      {remainingItems.length > 0 ? (
        <details className="group rounded-md border border-dashed border-slate-300 bg-slate-50/60">
          <summary className="flex cursor-pointer list-none items-center justify-between px-3 py-3 text-sm font-semibold text-slate-700">
            <span>
              {remainingAreAllUnsupported
                ? `展开 ${remainingItems.length} 项缺少简历证明的能力`
                : `展开余下 ${remainingItems.length} 项能力`}
            </span>
            <span className="flex h-7 w-7 items-center justify-center rounded-full border border-slate-200 bg-white">
              <ChevronDown size={16} className="text-slate-600 transition group-open:rotate-180" />
            </span>
          </summary>
          <div className="space-y-2 border-t border-dashed border-slate-300 p-2">
            <CapabilitySection
              items={remainingItems}
              evidenceButton={evidenceButton}
              getUpdate={getUpdate}
              getScoreUpdate={getScoreUpdate}
            />
          </div>
        </details>
      ) : null}
    </div>
  );
}

interface CapabilityItem {
  capability: JobCapabilityView;
  sources: JobRequirementView[];
}

function CapabilitySection({
  title,
  items,
  evidenceButton,
  getUpdate,
  getScoreUpdate
}: {
  title?: string;
  items: CapabilityItem[];
  evidenceButton: (ids: string[]) => ReactNode;
  getUpdate?: (targetIds: string[]) => CapabilityUpdateView | undefined;
  /** V2/V3 已发布的能力分变化；与面评证据提示并列展示。 */
  getScoreUpdate?: (targetIds: string[]) => CapabilityUpdateView | undefined;
}) {
  return (
    <div className="space-y-2">
      {title ? <div className="px-1 text-xs font-semibold text-slate-700">{title}</div> : null}
      {items.length > 0 ? items.map((item) => (
        <CapabilityCard
          key={item.capability.capabilityId}
          item={item}
          evidenceButton={evidenceButton}
          getUpdate={getUpdate}
          getScoreUpdate={getScoreUpdate}
        />
      )) : <p className="rounded-md bg-slate-50 px-3 py-2 text-xs text-muted">暂无能力项。</p>}
    </div>
  );
}

function CapabilityCard({
  item,
  evidenceButton,
  getUpdate,
  getScoreUpdate
}: {
  item: CapabilityItem;
  evidenceButton: (ids: string[]) => ReactNode;
  getUpdate?: (targetIds: string[]) => CapabilityUpdateView | undefined;
  /** V2/V3 已发布的能力分变化；与面评证据提示并列展示。 */
  getScoreUpdate?: (targetIds: string[]) => CapabilityUpdateView | undefined;
}) {
  const { capability, sources } = item;
  const evidenceIds = capabilityEvidenceIds(capability);
  const targetIds = [capability.capabilityId, ...sources.map((source) => source.jobUnitId)];
  const update = getUpdate?.(targetIds);
  const scoreUpdate = getScoreUpdate?.(targetIds);
  const match = matchPresentation(capability.score);

  return (
    <details className="group rounded-md border border-line bg-white">
      <summary className="cursor-pointer list-none p-3">
        <div className="grid grid-cols-[minmax(0,1fr)_auto] items-start gap-x-3 gap-y-2">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm font-semibold text-ink">{capability.name}</span>
              <span className="text-[11px] text-muted">{capability.role === "core" ? "核心" : "辅助"}</span>
            </div>
          </div>
          <div className="flex shrink-0 items-center justify-end gap-2">
            <Badge className={match.tone}>{match.label}</Badge>
            <span className="flex h-7 w-7 items-center justify-center rounded-full border border-slate-200 bg-slate-50">
              <ChevronDown size={16} className="text-slate-600 transition group-open:rotate-180" />
            </span>
          </div>
          <p className="col-span-2 line-clamp-2 text-xs leading-5 text-slate-600">
            {shortConclusion(capability.contentFitDescription)}
          </p>
          <div className="col-span-2 flex flex-wrap items-center gap-x-3 gap-y-2 border-t border-slate-100 pt-2">
            <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted">
              <span>{evidenceQualityLabel(capability)}</span>
              <span>{evidenceIds.length} 条证据</span>
              <span>来自 {sources.length} 条岗位要求</span>
            </div>
            {update || scoreUpdate ? (
              <div className="flex flex-wrap items-center gap-2">
                {update ? <Badge className={update.tone}>{update.label}</Badge> : null}
                {scoreUpdate ? <Badge className={scoreUpdate.tone}>{scoreUpdate.label}</Badge> : null}
              </div>
            ) : null}
          </div>
        </div>
      </summary>

      <div className="space-y-3 border-t border-line p-3">
        {capability.definition ? (
          <p className="text-xs leading-5 text-slate-700">
            <span className="font-semibold">能力定义：</span>{capability.definition}
          </p>
        ) : null}
        <p className="text-xs leading-5 text-slate-700">
          <span className="font-semibold">匹配判断：</span>{capability.contentFitDescription || "暂无"}
        </p>
        <p className="text-xs leading-5 text-slate-700">
          <span className="font-semibold">证据可靠性：</span>{capability.evidenceQualityDescription || "暂无"}
        </p>
        {evidenceButton(evidenceIds)}
        {update?.detail}
        {scoreUpdate?.detail}
        <div>
          <div className="text-xs font-semibold text-slate-700">来源岗位要求</div>
          <div className="mt-2 space-y-2">
            {sources.map((source) => (
              <div key={source.jobUnitId} className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2">
                <div className="text-[11px] font-semibold text-muted">
                  {source.sourceSection || "岗位要求"}
                </div>
                <p className="mt-1 text-xs leading-5 text-slate-700">{source.sourceText}</p>
              </div>
            ))}
          </div>
        </div>
      </div>
    </details>
  );
}

function CapabilityTags({
  title,
  items,
  emptyText,
  warning = false
}: {
  title: string;
  items: CapabilityItem[];
  emptyText: string;
  warning?: boolean;
}) {
  return (
    <div className="min-w-0">
      <div className="text-xs font-semibold text-muted">{title}</div>
      <div className="mt-2 flex flex-wrap gap-2">
        {items.length > 0 ? items.map(({ capability }) => (
          <span
            key={capability.capabilityId}
            title={capability.name}
            className={`inline-flex items-center whitespace-nowrap rounded-md border px-2.5 py-1 text-xs font-medium leading-5 ${
              warning
                ? "border-amber-200 bg-amber-50 text-amber-800"
                : "border-emerald-200 bg-emerald-50 text-emerald-700"
            }`}
          >
            {compactName(capability.name)}
          </span>
        )) : <span className="text-xs text-muted">{emptyText}</span>}
      </div>
    </div>
  );
}

function capabilityItems(requirements: JobRequirementView[]): CapabilityItem[] {
  const byId = new Map<string, CapabilityItem>();
  requirements.forEach((source) => {
    [...source.coreCapabilities, ...source.supportingCapabilities].forEach((capability) => {
      const current = byId.get(capability.capabilityId);
      if (current) {
        if (!current.sources.some((item) => item.jobUnitId === source.jobUnitId)) current.sources.push(source);
        if ((capability.score ?? -1) > (current.capability.score ?? -1)) current.capability = capability;
      } else {
        byId.set(capability.capabilityId, { capability, sources: [source] });
      }
    });
  });
  return [...byId.values()];
}

function sortByMatchStrength(items: CapabilityItem[]): CapabilityItem[] {
  return [...items].sort((left, right) => {
    const leftScore = left.capability.score ?? -1;
    const rightScore = right.capability.score ?? -1;
    return rightScore - leftScore;
  });
}

function hasResumeSupport(item: CapabilityItem): boolean {
  return (item.capability.score ?? 0) > 0;
}

function capabilityEvidenceIds(capability: JobCapabilityView): string[] {
  return [...new Set(capability.evidence.flatMap((item) =>
    item.sourceEvidenceIds.length > 0 ? item.sourceEvidenceIds : [item.evidenceId]
  ).filter(Boolean))];
}

function compactName(name: string): string {
  return name.length > 10 ? `${name.slice(0, 10)}…` : name;
}

function shortConclusion(description: string): string {
  if (!description.trim()) return "简历中暂未发现足以支持该能力的明确经历。";
  const firstSentence = description.split(/[。！？\n]/)[0].trim();
  return firstSentence.length > 42 ? `${firstSentence.slice(0, 42)}…` : firstSentence;
}

function matchPresentation(score: number | null | undefined): { label: string; tone: string } {
  if (typeof score !== "number" || !Number.isFinite(score)) {
    return { label: "待人工核验", tone: "border-slate-200 bg-slate-50 text-slate-600" };
  }
  if (score >= 85) return { label: "高度匹配", tone: "border-emerald-200 bg-emerald-50 text-emerald-700" };
  if (score >= 65) return { label: "基本匹配", tone: "border-blue-200 bg-blue-50 text-blue-700" };
  if (score > 0) return { label: "部分匹配", tone: "border-amber-200 bg-amber-50 text-amber-800" };
  return { label: "缺少简历证明", tone: "border-rose-200 bg-rose-50 text-rose-700" };
}

function evidenceQualityLabel(capability: JobCapabilityView): string {
  const qualityScores = capability.evidence
    .map((item) => item.qualityScore)
    .filter((value): value is number => typeof value === "number" && Number.isFinite(value));
  const bestQuality = qualityScores.length > 0 ? Math.max(...qualityScores) : 0;
  if (bestQuality >= 80) return "结果与过程较明确";
  if (bestQuality >= 60) return "有具体经历支持";
  if (bestQuality > 0 || capability.evidence.length > 0) return "证明较弱";
  return "暂无直接证明";
}
