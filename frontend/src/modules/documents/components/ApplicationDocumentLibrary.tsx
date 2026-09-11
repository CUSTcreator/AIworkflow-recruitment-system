import { FilePlus2, FileText, MoreHorizontal, Search, Upload, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  deleteApplicationDocument,
  deleteCandidateDocument,
  getApplicationDocuments,
  loadApplicationDocumentPages,
  renameCandidateDocument,
  renameApplicationDocument,
  uploadApplicationDocument,
  type ApplicationDocument,
  type ApplicationDocumentCategory,
  type ApplicationUploadCategory,
  type ApplicationDocumentSourceStage,
  type ApplicationDocumentsView
} from "@/modules/documents/api";
import { useAuth } from "@/modules/auth/AuthProvider";

const categoryLabels: Record<ApplicationDocumentCategory, string> = {
  resume: "候选人简历",
  standard_resume: "标准简历",
  psychological_assessment: "心理测评",
  academic_transcript: "本硕成绩单",
  certificate: "证书",
  portfolio: "作品集",
  other: "其他资料",
  job: "岗位资料"
};

export function ApplicationDocumentLibrary({
  open,
  applicationId,
  candidateName,
  mode = "manage",
  /** 由调用页面显式声明来源阶段，禁止从 URL 路径猜测业务语义。 */
  sourceStage = "manual_upload",
  openDocumentIds = [],
  onClose,
  onOpenDocument
}: {
  open: boolean;
  applicationId: string;
  candidateName?: string;
  mode?: "picker" | "manage";
  sourceStage?: Exclude<ApplicationDocumentSourceStage, "import" | "job">;
  openDocumentIds?: string[];
  onClose: () => void;
  onOpenDocument?: (document: ApplicationDocument) => void;
}) {
  const { token } = useAuth();
  const [view, setView] = useState<ApplicationDocumentsView>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [uploadMode, setUploadMode] = useState(false);
  const [file, setFile] = useState<File>();
  const [displayName, setDisplayName] = useState("");
  const [category, setCategory] = useState<ApplicationUploadCategory>("other");
  const [note, setNote] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [menuId, setMenuId] = useState<string>();
  const [preview, setPreview] = useState<{ document: ApplicationDocument; pages: string[] }>();
  const opened = useMemo(() => new Set(openDocumentIds), [openDocumentIds]);

  const load = useCallback(async () => {
    if (!token || !open) return;
    setLoading(true);
    setError("");
    try {
      setView(await getApplicationDocuments(token, applicationId));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "候选人材料加载失败");
    } finally {
      setLoading(false);
    }
  }, [applicationId, open, token]);

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setUploadMode(false);
    setMenuId(undefined);
    setPreview(undefined);
    void load();
  }, [load, open]);

  useEffect(() => {
    if (!open) return;
    const closeMenu = () => setMenuId(undefined);
    window.addEventListener("resize", closeMenu);
    return () => window.removeEventListener("resize", closeMenu);
  }, [open]);


  useEffect(() => () => {
    preview?.pages.forEach((page) => URL.revokeObjectURL(page));
  }, [preview]);
  const filtered = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    if (!keyword) return view?.documents ?? [];
    return (view?.documents ?? []).filter((document) =>
      `${document.displayName} ${document.filename} ${categoryLabels[document.category]}`.toLowerCase().includes(keyword)
    );
  }, [query, view]);

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
      const created = await uploadApplicationDocument(token, applicationId, {
        file,
        displayName: displayName.trim(),
        category,
        sourceStage,
        note,
      });
      resetUpload();
      await load();
      if (mode === "picker" && onOpenDocument) {
        onOpenDocument(created);
        onClose();
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "上传失败");
    } finally {
      setSubmitting(false);
    }
  }

  async function renameDocument(document: ApplicationDocument) {
    if (!token || !document.canRename) return;
    const nextName = window.prompt("资料名称", document.displayName)?.trim();
    if (!nextName || nextName === document.displayName) return;
    try {
      if (document.scope === "candidate") {
        if (!view?.candidateId) return;
        await renameCandidateDocument(token, view.candidateId, document.documentId, nextName);
      } else {
        await renameApplicationDocument(token, applicationId, document.documentId, nextName);
      }
      setMenuId(undefined);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "重命名失败");
    }
  }

  async function previewDocument(document: ApplicationDocument) {
    if (!token) return;
    setError("");
    try {
      const pages = await loadApplicationDocumentPages(token, document);
      setPreview({ document, pages });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "资料预览失败");
    }
  }

  function closePreview() {
    preview?.pages.forEach((page) => URL.revokeObjectURL(page));
    setPreview(undefined);
  }

  async function removeDocument(document: ApplicationDocument) {
    if (!token || !document.canDelete) return;
    const message = document.scope === "candidate"
      ? `确认删除“${document.displayName}”吗？\n\n删除后，这份候选人资料会从该候选人的 ${document.deleteImpactCount ?? 1} 个有效岗位申请中消失。\n不会删除候选人、简历或申请记录。`
      : `确认删除“${document.displayName}”吗？`;
    if (!window.confirm(message)) return;
    try {
      if (document.scope === "candidate") {
        if (!view?.candidateId) return;
        await deleteCandidateDocument(token, view.candidateId, document.documentId);
      } else {
        await deleteApplicationDocument(token, applicationId, document.documentId);
      }
      setMenuId(undefined);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "删除资料失败");
    }
  }

  if (!open) return null;

  const content = (
    <div className="flex h-full flex-col bg-white">
      <div className="flex items-start justify-between border-b border-line px-5 py-4">
        <div>
          <h2 className="text-lg font-semibold text-ink">候选人材料</h2>
          <p className="mt-1 text-sm text-muted">{candidateName ? `${candidateName} · ` : ""}包含候选人共享资料和当前申请专属资料</p>
        </div>
        <button type="button" aria-label="关闭候选人材料" onClick={onClose} className="rounded-md p-1.5 text-slate-500 hover:bg-slate-100"><X size={18} /></button>
      </div>

      <div className="flex items-center gap-2 border-b border-line px-5 py-3">
        <label className="relative min-w-0 flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" size={15} />
          <input className="h-9 w-full rounded-md border border-slate-300 pl-9 pr-3 text-sm outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100" placeholder="搜索资料名称或文件名" value={query} onChange={(event) => setQuery(event.target.value)} />
        </label>
        {view?.permissions.canUpload ? (
          <button type="button" onClick={() => setUploadMode((value) => !value)} className="inline-flex h-9 shrink-0 items-center gap-1.5 rounded-md bg-blue-600 px-3 text-sm font-semibold text-white hover:bg-blue-700">
            <Upload size={15} />上传申请资料
          </button>
        ) : null}
      </div>

      {uploadMode ? (
        <div className="border-b border-blue-100 bg-blue-50/60 px-5 py-4">
          <p className="mb-3 text-xs leading-5 text-blue-800">上传到当前申请，仅在本次岗位招聘流程中使用。</p>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="text-sm font-medium text-slate-700">资料名称
              <input className="mt-1.5 h-9 w-full rounded-md border border-slate-300 bg-white px-3 text-sm outline-none focus:border-blue-400" value={displayName} maxLength={128} onChange={(event) => setDisplayName(event.target.value)} placeholder="例如：技术一面附件" />
            </label>
            <label className="text-sm font-medium text-slate-700">资料分类
              <select className="mt-1.5 h-9 w-full rounded-md border border-slate-300 bg-white px-3 text-sm" value={category} onChange={(event) => setCategory(event.target.value as typeof category)}>
                <option value="standard_resume">标准简历</option><option value="psychological_assessment">心理测评</option><option value="academic_transcript">本硕成绩单</option><option value="other">其他资料</option>
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
            <button type="button" disabled={!file || !displayName.trim() || submitting} onClick={() => void submitUpload()} className="h-9 rounded-md bg-blue-600 px-3 text-sm font-semibold text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50">{submitting ? "上传中…" : mode === "picker" ? "上传并打开" : "上传申请资料"}</button>
          </div>
        </div>
      ) : null}

      {error ? <div className="mx-5 mt-3 rounded-md bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</div> : null}
      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
        {loading ? <div className="py-10 text-center text-sm text-muted">正在加载候选人材料…</div> : null}
        {!loading && filtered.length === 0 ? <div className="py-10 text-center text-sm text-muted">没有匹配的候选人材料。</div> : null}
        {!loading ? (["candidate", "application", "job"] as const).map((scope) => {
          const rows = filtered.filter((document) => document.scope === scope);
          if (rows.length === 0) return null;
          const scopeDescription = scope === "candidate"
            ? "随候选人共享，在该候选人的所有有效申请中展示"
            : scope === "application"
              ? "仅属于当前岗位申请，不会同步到其他申请"
              : "岗位相关资料，供该岗位招聘流程查看";
          return (
            <section key={scope} className="mb-5 last:mb-0">
              <div className="mb-2">
                <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">{scope === "candidate" ? "候选人资料" : scope === "application" ? "本次申请资料" : "岗位资料"}</h3>
                <p className="mt-1 text-xs leading-5 text-muted">{scopeDescription}</p>
              </div>
              <div className="space-y-1.5">
                {rows.map((document) => (
                  <div key={document.documentId} className="flex items-center gap-3 rounded-lg border border-slate-200 px-3 py-2.5 transition hover:border-blue-200 hover:bg-blue-50/30">
                    <div className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-md ${document.scope === "job" ? "bg-violet-50 text-violet-700" : document.scope === "candidate" ? "bg-emerald-50 text-emerald-700" : "bg-blue-50 text-blue-700"}`}><FileText size={17} /></div>
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-semibold text-ink">{document.displayName}</div>
                      <div className="mt-0.5 truncate text-xs text-muted">{categoryLabels[document.category]} · {document.uploadedByName || "系统"} · {new Date(document.uploadedAt).toLocaleDateString("zh-CN")}</div>
                    </div>
                    {mode === "picker" ? (
                      <button type="button" onClick={() => { onOpenDocument?.(document); onClose(); }} className={`h-8 rounded-md px-2.5 text-xs font-semibold ${opened.has(document.documentId) ? "bg-slate-100 text-slate-600" : "bg-blue-50 text-blue-700 hover:bg-blue-100"}`}>{opened.has(document.documentId) ? "切换" : "打开"}</button>
                    ) : (
                      <button type="button" onClick={() => void previewDocument(document)} className="h-8 rounded-md bg-blue-50 px-2.5 text-xs font-semibold text-blue-700 hover:bg-blue-100">打开</button>
                    )}
                    {(document.canRename || document.canDelete) ? (
                      <div className="relative">
                        <button type="button" aria-label={`管理${document.displayName}`} onClick={() => setMenuId((current) => current === document.documentId ? undefined : document.documentId)} className="flex h-8 w-8 items-center justify-center rounded-md text-slate-500 hover:bg-slate-100"><MoreHorizontal size={16} /></button>
                        {menuId === document.documentId ? (
                          <div className="absolute right-0 top-9 z-20 w-32 rounded-md border border-line bg-white p-1 text-sm shadow-lg">
                            {document.canRename ? <button type="button" onClick={() => void renameDocument(document)} className="w-full rounded px-3 py-2 text-left text-slate-700 hover:bg-slate-50">重命名</button> : null}
                            {document.canDelete ? <button type="button" onClick={() => void removeDocument(document)} className="w-full rounded px-3 py-2 text-left text-slate-700 hover:bg-rose-50 hover:text-rose-600">删除资料</button> : null}
                          </div>
                        ) : null}
                      </div>
                    ) : null}
                  </div>
                ))}
              </div>
            </section>
          );
        }) : null}
      </div>
    </div>
  );

  return (
    <>
      <div className={`fixed inset-0 z-50 bg-slate-950/35 ${mode === "manage" ? "flex justify-end" : "flex items-center justify-center p-4"}`} onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
        <div className={mode === "manage" ? "h-full w-full max-w-xl shadow-2xl" : "h-[min(760px,88vh)] w-full max-w-3xl overflow-hidden rounded-xl border border-line shadow-2xl"}>{content}</div>
      </div>
      {preview ? <div className="fixed inset-0 z-[60] flex items-center justify-center bg-slate-950/60 p-4" onMouseDown={(event) => { if (event.target === event.currentTarget) closePreview(); }}>
        <div className="flex max-h-[90vh] w-full max-w-4xl flex-col overflow-hidden rounded-lg bg-white shadow-2xl">
          <div className="flex items-center justify-between border-b border-line px-5 py-3"><div className="truncate text-sm font-semibold text-ink">{preview.document.displayName}</div><button type="button" aria-label="关闭预览" onClick={closePreview} className="rounded-md p-1.5 text-slate-500 hover:bg-slate-100"><X size={18} /></button></div>
          <div className="min-h-0 space-y-4 overflow-y-auto bg-slate-100 p-4">{preview.pages.map((page, index) => <img key={page} src={page} alt={`${preview.document.displayName}第${index + 1}页`} className="mx-auto block max-w-full bg-white shadow-sm" />)}</div>
        </div>
      </div> : null}
    </>
  );
}
