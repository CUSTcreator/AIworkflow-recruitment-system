import { HelpCircle, X } from 'lucide-react';
import { useState } from "react";

type ScoreStage = "screening" | "after_first_interview" | "after_second_interview";

interface ScoreExplanationButtonProps {
  stage: ScoreStage;
  total: number | null | undefined;
  jobFit: number | null | undefined;
  resumeExperience: number | null | undefined;
  education: number | null | undefined;
  comprehensiveScore?: number | null;
  baselineTotal?: number | null;
  baselineLabel?: string;
}

const components = [
  { key: "job", label: "岗位要求能力", weight: 0.5 },
  { key: "resume", label: "预设经历能力", weight: 0.35 },
  { key: "education", label: "学历背景", weight: 0.15 }
] as const;

function scoreText(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toFixed(1)
    : "待核验";
}

export function ScoreExplanationButton({
  stage,
  total,
  jobFit,
  resumeExperience,
  education,
  baselineTotal,
  baselineLabel = "初步筛选结果",
}: ScoreExplanationButtonProps) {
  const [open, setOpen] = useState(false);
  const values = { job: jobFit, resume: resumeExperience, education };
  const delta =
    typeof total === "number" && typeof baselineTotal === "number"
      ? total - baselineTotal
      : undefined;

  return (
    <>
      <button
        type="button"
        aria-label="查看候选人能力分计算说明"
        title="查看计算说明"
        className="inline-flex h-5 w-5 items-center justify-center rounded-full text-slate-400 hover:bg-slate-100 hover:text-blue-700"
        onClick={() => setOpen(true)}
      >
        <HelpCircle size={15} />
      </button>
      {open ? (
        <div
          className="fixed inset-0 z-[80] flex items-center justify-center bg-slate-950/45 p-4"
          role="dialog"
          aria-modal="true"
          onMouseDown={(event) => {
            if (event.currentTarget === event.target) setOpen(false);
          }}
        >
          <div className="w-full max-w-xl rounded-xl bg-white shadow-2xl">
            <header className="flex items-start justify-between border-b border-line px-5 py-4">
              <div>
                <h2 className="font-semibold text-ink">候选人能力分如何计算</h2>
                <p className="mt-1 text-xs text-muted">
                  {stage === "screening"
                    ? "初步筛选结果"
                    : stage === "after_first_interview"
                      ? "一面评估后"
                      : "二面评估后"}
                </p>
              </div>
              <button
                type="button"
                aria-label="关闭"
                className="rounded-md p-1 text-muted hover:bg-slate-100"
                onClick={() => setOpen(false)}
              >
                <X size={18} />
              </button>
            </header>
            <div className="space-y-4 p-5">
              <div className="rounded-lg border border-blue-100 bg-blue-50 px-4 py-3">
                <div className="text-xs font-semibold text-blue-700">候选人能力分</div>
                <div className="mt-1 text-3xl font-semibold text-blue-950">
                  {scoreText(total)}
                </div>
                {delta !== undefined ? (
                  <div className="mt-1 text-xs text-blue-800">
                    相比{baselineLabel}
                    {Math.abs(delta) < 0.05
                      ? "无变化"
                      : `${delta > 0 ? "提高" : "降低"} ${Math.abs(delta).toFixed(1)} 分`}
                  </div>
                ) : null}
              </div>
              <div className="overflow-hidden rounded-md border border-line">
                <table className="data-table w-full text-left text-xs">
                  <thead className="bg-slate-50 text-muted">
                    <tr>
                      <th className="px-3 py-2">分项</th>
                      <th className="px-3 py-2 text-right">分数</th>
                      <th className="px-3 py-2 text-right">权重</th>
                      <th className="px-3 py-2 text-right">计入能力分</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-line">
                    {components.map((item) => {
                      const value = values[item.key];
                      return (
                        <tr key={item.key}>
                          <td className="px-3 py-2">{item.label}</td>
                          <td className="px-3 py-2 text-right">{scoreText(value)}</td>
                          <td className="px-3 py-2 text-right">{item.weight * 100}%</td>
                          <td className="px-3 py-2 text-right font-semibold">
                            {typeof value === "number"
                              ? (value * item.weight).toFixed(1)
                              : "—"}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <p className="text-xs leading-5 text-slate-700">
          候选人能力分 = 岗位要求能力 × 50% + 预设经历能力 × 35% + 学历背景 × 15%。
                非能力信息、风险项和硬性筛选结果均不直接加减该分数。
              </p>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
