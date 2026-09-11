import { FilePlus2, FileText, MoreHorizontal, Upload, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { useAuth } from "@/modules/auth/AuthProvider";
import { AuthenticatedPdfLink } from "@/modules/documents/components/AuthenticatedPdfLink";
import {
  deleteCandidateDocument,
  getCandidateDocuments,
  loadApplicationDocumentPages,
  renameCandidateDocument,
  uploadCandidateDocument,
  type ApplicationDocument,
  type CandidateDocumentCategory,
  type CandidateDocumentsView,
} from "@/modules/documents/api";

const categoryLabels: Record<CandidateDocumentCategory, string> = {
  psychological_assessment: "心理测评",
  academic_transcript: "成绩单",
  certificate: "证书",
  portfolio: "作品集",
  other: "其他资料",
};

export function CandidateDocumentLibrary({
  open,
  candidateId,
  candidateName,
  onClose,
}: {
  open: boolean;
  candidateId: string;
  candidateName?: string;
  onClose: () => void;
}) {
  const { token } = useAuth();
  const [view, setView] = useState<CandidateDocumentsView>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [uploadMode, setUploadMode] = useState(false);
  const [file, setFile] = useState<File>();
  const [displayName, setDisplayName] = useState("");
  const [category, setCategory] = useState<CandidateDocumentCategory>("other");
  const [note, setNote] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [menuId, setMenuId] = useState<string>();
  const [preview, setPreview] = useState<{ document: ApplicationDocument; pages: string[] }>();

  const load = useCallback(async () => {
    if (!token || !open) return;
    setLoading(true);
    setError("");
    try {
      setView(await getCandidateDocuments(token, candidateId));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "候选人资料加载失败");
    } finally {
      setLoading(false);
    }
  }, [candidateId, open, token]);

  useEffect(() => {
    if (!open) return;
    setUploadMode(false);
    setMenuId(undefined);
    setPreview(undefined);
    void load();
  }, [load, open]);

  useEffect(() => () => {
    preview?.pages.forEach((page) => URL.revokeObjectURL(page));
  }, [preview]);

  function resetUpload() {
    setUploadMode(false);
    setFile(undefined);
    setDisplayName("");
    setCategory("other");
    setNote("");
  }

  async function submitUpload() {
    if (!token || !file || !displayName.trim() || submitting) return;
    setSubmitting(true);
    setError("");
    try {
      await uploadCandidateDocument(token, candidateId, {
        file,
        displayName: displayName.trim(),
        category,
        note,
      });
      resetUpload();
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "上传失败");
    } finally {
      setSubmitting(false);
    }
  }

  async function openDocument(document: ApplicationDocument) {
    if (!token) return;
    setMenuId(undefined);
    setError("");
    try {
      const pages = await loadApplicationDocumentPages(token, document);
      setPreview({ document, pages });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "资料预览失败");
    }
  }

  async function renameDocument(document: ApplicationDocument) {
    if (!token || !document.canRename) return;
    const nextName = window.prompt("资料名称", document.displayName)?.trim();
    if (!nextName || nextName === document.displayName) return;
    try {
      await renameCandidateDocument(token, candidateId, document.documentId, nextName);
      setMenuId(undefined);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "重命名失败");
    }
  }

  async function removeDocument(document: ApplicationDocument) {
    if (!token || !document.canDelete) return;
    const count = view?.activeApplicationCount ?? 0;
    const impact = count > 0
      ? `删除后，这份资料会从该候选人的 ${count} 个有效岗位申请中消失。`
      : "删除后，这份资料将从候选人档案中消失。";
    if (!window.confirm(`确认删除“${document.displayName}”吗？\n\n${impact}\n不会删除候选人、简历或申请记录。`)) return;
    try {
      await deleteCandidateDocument(token, candidateId, document.documentId);
      setMenuId(undefined);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "删除资料失败");
    }
  }

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-slate-950/35" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <aside className="flex h-full w-full max-w-xl flex-col bg-white shadow-2xl" role="dialog" aria-modal="true" aria-labelledby="candidate-documents-title">
        <div className="flex items-start justify-between border-b border-line px-5 py-4">
          <div>
            <h2 id="candidate-documents-title" className="text-lg font-semibold text-ink">候选人资料</h2>
            <p className="mt-1 text-sm text-muted">{candidateName || "候选人"} · 长期资料，与具体岗位申请无关</p>
          </div>
          <button type="button" aria-label="关闭候选人资料" onClick={onClose} className="rounded-md p-1.5 text-slate-500 hover:bg-slate-100"><X size={18} /></button>
        </div>

        <div className="flex items-center justify-between border-b border-line px-5 py-3">
          <p className="text-xs text-muted">这些资料会在该候选人的所有有效申请中展示。</p>
          {view?.permissions.canUpload ? <button type="button" onClick={() => setUploadMode((value) => !value)} className="inline-flex h-9 shrink-0 items-center gap-1.5 rounded-md bg-blue-600 px-3 text-sm font-semibold text-white hover:bg-blue-700"><Upload size={15} />上传个人资料</button> : null}
        </div>

      {uploadMode ? (
          <div className="border-b border-blue-100 bg-blue-50/60 px-5 py-4">
            <p className="mb-3 text-xs leading-5 text-blue-800">上传为候选人长期资料，将在其所有有效申请中展示。</p>
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="text-sm font-medium text-slate-700">资料名称
                <input className="mt-1.5 h-9 w-full rounded-md border border-slate-300 bg-white px-3 text-sm outline-none focus:border-blue-400" value={displayName} maxLength={128} onChange={(event) => setDisplayName(event.target.value)} placeholder="例如：本硕成绩单" />
              </label>
              <label className="text-sm font-medium text-slate-700">资料分类
                <select className="mt-1.5 h-9 w-full rounded-md border border-slate-300 bg-white px-3 text-sm" value={category} onChange={(event) => setCategory(event.target.value as CandidateDocumentCategory)}>
                  {Object.entries(categoryLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                </select>
              </label>
            </div>
            <label className="mt-3 block text-sm font-medium text-slate-700">PDF文件
              <label className="mt-1.5 flex h-10 cursor-pointer items-center gap-2 rounded-md border border-dashed border-slate-300 bg-white px-3 text-sm text-slate-600 hover:border-blue-300 hover:bg-blue-50/50">
                <FilePlus2 size={16} /><span className="min-w-0 truncate">{file?.name ?? "选择PDF文件"}</span>
                <input type="file" accept="application/pdf,.pdf" className="hidden" onChange={(event) => { const selected = event.target.files?.[0]; setFile(selected); if (selected && !displayName) setDisplayName(selected.name.replace(/\.pdf$/i, "")); }} />
              </label>
            </label>
            <label className="mt-3 block text-sm font-medium text-slate-700">备注（可选）
              <input className="mt-1.5 h-9 w-full rounded-md border border-slate-300 bg-white px-3 text-sm outline-none focus:border-blue-400" value={note} maxLength={500} onChange={(event) => setNote(event.target.value)} />
            </label>
            <div className="mt-3 flex justify-end gap-2">
              <button type="button" onClick={resetUpload} className="h-9 rounded-md border border-slate-300 bg-white px-3 text-sm font-semibold text-slate-700 hover:bg-slate-50">取消</button>
              <button type="button" disabled={!file || !displayName.trim() || submitting} onClick={() => void submitUpload()} className="h-9 rounded-md bg-blue-600 px-3 text-sm font-semibold text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50">{submitting ? "上传中…" : "上传"}</button>
            </div>
          </div>
        ) : null}

        {error ? <div className="mx-5 mt-3 whitespace-pre-line rounded-md bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</div> : null}
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          <section className="mb-5">
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">当前简历</h3>
            {view?.currentResume ? (
              <div className="flex items-center gap-3 rounded-lg border border-slate-200 px-3 py-2.5">
                <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-blue-50 text-blue-700"><FileText size={17} /></div>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-semibold text-ink">{view.currentResume.filename}</div>
                  <div className="mt-0.5 text-xs text-muted">当前采用版本 · {new Date(view.currentResume.uploadedAt).toLocaleDateString("zh-CN")}</div>
                  <AuthenticatedPdfLink url={view.currentResume.pdfUrl} />
                </div>
              </div>
            ) : <div className="rounded-lg border border-dashed border-slate-300 px-3 py-5 text-center text-sm text-muted">暂无当前简历</div>}
          </section>
          <section>
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">个人资料</h3>
          {loading ? <div className="py-10 text-center text-sm text-muted">正在加载候选人资料…</div> : null}
          {!loading && !view?.documents.length ? <div className="py-12 text-center text-sm text-muted">尚未上传候选人的其他资料。</div> : null}
          {!loading ? <div className="space-y-2">{view?.documents.map((document) => (
            <div key={document.documentId} className="flex items-center gap-3 rounded-lg border border-slate-200 px-3 py-2.5 transition hover:border-emerald-200 hover:bg-emerald-50/30">
              <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-emerald-50 text-emerald-700"><FileText size={17} /></div>
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-semibold text-ink">{document.displayName}</div>
                <div className="mt-0.5 truncate text-xs text-muted">{categoryLabels[document.category as CandidateDocumentCategory] ?? "其他资料"} · {document.uploadedByName || "系统"} · {new Date(document.uploadedAt).toLocaleDateString("zh-CN")}</div>
              </div>
              <button type="button" onClick={() => void openDocument(document)} className="h-8 rounded-md bg-emerald-50 px-2.5 text-xs font-semibold text-emerald-700 hover:bg-emerald-100">打开</button>
              {(document.canRename || document.canDelete) ? <div className="relative">
                <button type="button" aria-label={`管理${document.displayName}`} onClick={() => setMenuId((current) => current === document.documentId ? undefined : document.documentId)} className="flex h-8 w-8 items-center justify-center rounded-md text-slate-500 hover:bg-slate-100"><MoreHorizontal size={16} /></button>
                {menuId === document.documentId ? <div className="absolute right-0 top-9 z-20 w-32 rounded-md border border-line bg-white p-1 text-sm shadow-lg">
                  {document.canRename ? <button type="button" onClick={() => void renameDocument(document)} className="w-full rounded px-3 py-2 text-left text-slate-700 hover:bg-slate-50">重命名</button> : null}
                  {document.canDelete ? <button type="button" onClick={() => void removeDocument(document)} className="w-full rounded px-3 py-2 text-left text-rose-600 hover:bg-rose-50">删除资料</button> : null}
                </div> : null}
              </div> : null}
            </div>
          ))}</div> : null}
          </section>
        </div>
      </aside>

      {preview ? <div className="fixed inset-0 z-[60] flex items-center justify-center bg-slate-950/60 p-4" onMouseDown={(event) => { if (event.target === event.currentTarget) { preview.pages.forEach((page) => URL.revokeObjectURL(page)); setPreview(undefined); } }}>
        <div className="flex max-h-[90vh] w-full max-w-4xl flex-col overflow-hidden rounded-lg bg-white shadow-2xl">
          <div className="flex items-center justify-between border-b border-line px-5 py-3"><div className="truncate text-sm font-semibold text-ink">{preview.document.displayName}</div><button type="button" aria-label="关闭预览" onClick={() => { preview.pages.forEach((page) => URL.revokeObjectURL(page)); setPreview(undefined); }} className="rounded-md p-1.5 text-slate-500 hover:bg-slate-100"><X size={18} /></button></div>
          <div className="min-h-0 space-y-4 overflow-y-auto bg-slate-100 p-4">{preview.pages.map((page, index) => <img key={page} src={page} alt={`${preview.document.displayName}第${index + 1}页`} className="mx-auto block max-w-full bg-white shadow-sm" />)}</div>
        </div>
      </div> : null}
    </div>
  );
}
