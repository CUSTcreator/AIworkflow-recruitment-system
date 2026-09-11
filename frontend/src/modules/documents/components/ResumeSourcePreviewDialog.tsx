import { useEffect, useState } from "react";
import { FileText, RefreshCw, X } from "lucide-react";
import { getResumeCorrectionDraft, type ResumeCorrectionDraft } from "@/modules/documents/resumeApi";
import { useAuth } from "@/modules/auth/AuthProvider";
import { Button } from "@/shared/ui/Button";

/**
 * 只读展示简历结构化来源。算法内部 ID 不在此处渲染；用户确认后再进入校正表单。
 */
export function ResumeSourcePreviewDialog({
  submissionId,
  candidateName,
  onClose,
  onEdit,
}: {
  submissionId: string;
  candidateName: string;
  onClose: () => void;
  onEdit: () => void;
}) {
  const { token } = useAuth();
  const [draft, setDraft] = useState<ResumeCorrectionDraft>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!token || !submissionId) return;
    setLoading(true);
    setError("");
    void getResumeCorrectionDraft(token, submissionId)
      .then(setDraft)
      .catch((reason) => setError(reason instanceof Error ? reason.message : "简历来源加载失败"))
      .finally(() => setLoading(false));
  }, [submissionId, token]);

  const facts = (draft?.candidateFacts ?? {}) as {
    education_records?: Array<Record<string, unknown>>;
    relevant_experience_years?: { value?: number };
  };
  const skills = draft?.skillClaims ?? [];

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-4" role="dialog" aria-modal="true" aria-labelledby="resume-source-preview-title">
      <div className="flex max-h-[88vh] w-full max-w-4xl flex-col overflow-hidden rounded-lg bg-white shadow-xl">
        <div className="flex items-start justify-between gap-3 border-b border-line p-5">
          <div>
            <h2 id="resume-source-preview-title" className="font-semibold text-ink">简历来源信息</h2>
            <p className="mt-1 text-sm text-muted">候选人：{candidateName}</p>
          </div>
          <button type="button" className="rounded-md p-2 text-muted hover:bg-slate-100" onClick={onClose} aria-label="关闭"><X size={18} /></button>
        </div>
        <div className="min-h-0 flex-1 overflow-auto p-5">
          {loading ? <p className="text-sm text-muted">正在加载简历来源…</p> : null}
          {error ? <p className="rounded-md bg-rose-50 p-3 text-sm text-rose-700">{error}</p> : null}
          {!loading && !error && draft ? (
            <div className="space-y-4">
              <section className="rounded-md border border-line p-3">
                <h3 className="text-sm font-semibold text-ink">教育与经验</h3>
                <div className="mt-2 space-y-2 text-sm text-slate-700">
                  {(facts.education_records ?? []).map((item, index) => <p key={index}>{String(item.school ?? "学校未记录")} · {String(item.degree_level ?? "学历未记录")} · {String(item.major ?? "专业未记录")}</p>)}
                  <p>相关经验年限：{facts.relevant_experience_years?.value ?? "未记录"}</p>
                </div>
              </section>
              <section className="rounded-md border border-line p-3">
                <h3 className="text-sm font-semibold text-ink">项目与工作经历</h3>
                <div className="mt-2 space-y-2 text-sm text-slate-700">
                  {draft.experienceUnits.map((item, index) => <div key={index}><p className="font-medium">{item.title || "未命名经历"}</p><p className="mt-1 text-xs text-muted">{item.sourceBullets.map((bullet) => bullet.text).filter(Boolean).join("；") || "暂无直接工作描述"}</p></div>)}
                  {!draft.experienceUnits.length ? <p className="text-muted">未识别到项目或工作经历。</p> : null}
                </div>
              </section>
              <section className="rounded-md border border-line p-3">
                <h3 className="text-sm font-semibold text-ink">技能声明</h3>
                <p className="mt-2 text-sm text-slate-700">{skills.map((item) => String(item.skill_name ?? "")).filter(Boolean).join("、") || "未识别到技能声明"}</p>
              </section>
              <details className="rounded-md border border-line bg-slate-50">
                <summary className="cursor-pointer px-3 py-2 text-sm font-medium text-ink">查看解析原文片段</summary>
                <div className="max-h-56 space-y-2 overflow-auto border-t border-line p-3 text-sm leading-6 text-slate-700">
                  {draft.sourceBlocks.map((block) => <p key={block.blockId} className="whitespace-pre-wrap">{block.text}</p>)}
                  {!draft.sourceBlocks.length ? <p className="text-muted">暂无可查看的原文片段。</p> : null}
                </div>
              </details>
            </div>
          ) : null}
        </div>
        <div className="flex justify-end gap-2 border-t border-line p-4">
          <Button onClick={onClose}>关闭</Button>
          <Button variant="primary" disabled={loading || Boolean(error)} onClick={onEdit}><FileText size={16} />进入校正</Button>
        </div>
      </div>
    </div>
  );
}
