import { ChevronDown, Clock3 } from "lucide-react";
import { useEffect, useState } from "react";
import { useAuth } from "@/modules/auth/AuthProvider";
import {
  getRecruitmentTimeline,
  type RecruitmentTimelineItem
} from "@/modules/applications/api";

interface Milestone {
  eventId: string;
  kind: string;
  label: string;
  result?: string;
  effectiveAt: string;
}

function toMilestone(item: RecruitmentTimelineItem): Milestone {
  return { eventId: item.eventId, kind: item.kind, label: item.label, effectiveAt: item.effectiveAt };
}

function compactTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false
  }).format(new Date(value));
}

export function RecruitmentMilestoneCard({ applicationId }: { applicationId: string }) {
  const { token } = useAuth();
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [milestones, setMilestones] = useState<Milestone[]>([]);
  const [loadedApplicationId, setLoadedApplicationId] = useState<string>();

  useEffect(() => {
    if (!open || !token || loadedApplicationId === applicationId || loading) return;
    setLoading(true);
    void getRecruitmentTimeline(token, applicationId)
      .then(({ items }) => {
        setMilestones(items.map(toMilestone));
      })
      .catch(() => setMilestones([]))
      .finally(() => {
        setLoadedApplicationId(applicationId);
        setLoading(false);
      });
  }, [applicationId, loadedApplicationId, loading, open, token]);

  return (
    <section className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-[0_1px_3px_rgba(15,23,42,0.06)]">
      <button
        type="button"
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <span className="flex items-center gap-2 text-sm font-semibold text-ink">
          <Clock3 size={15} className="text-slate-500" aria-hidden="true" />
          招聘历程
        </span>
        <ChevronDown size={16} className={`text-muted transition ${open ? "rotate-180" : ""}`} aria-hidden="true" />
      </button>
      {open ? (
        <div className="border-t border-slate-100 px-4 py-3">
          {loading ? <p className="text-xs text-muted">正在加载…</p> : null}
          {!loading && milestones.length === 0 ? <p className="text-xs text-muted">暂无已记录的招聘节点。</p> : null}
          {!loading && milestones.length > 0 ? (
            <div className="space-y-2">
              {milestones.map((item, index) => (
                <div key={item.eventId} className="flex items-center gap-2 text-xs">
                  <span className="relative flex h-4 w-3 shrink-0 items-center justify-center">
                    <span className="h-1.5 w-1.5 rounded-full bg-blue-600" />
                    {index < milestones.length - 1 ? <span className="absolute left-1/2 top-3 h-3 w-px -translate-x-1/2 bg-slate-200" /> : null}
                  </span>
                  <span className="min-w-0 flex-1 truncate font-medium text-slate-700">
                    {item.label}{item.result ? <span className="text-slate-500"> · {item.result}</span> : null}
                  </span>
                  <time className="shrink-0 tabular-nums text-slate-500">{compactTime(item.effectiveAt)}</time>
                </div>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
