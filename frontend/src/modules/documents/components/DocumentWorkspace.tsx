import {
  FileText,
  PanelLeftOpen,
  PanelRightOpen,
  Plus,
  RotateCcw,
  X,
  ZoomIn,
  ZoomOut
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import {
  APPLICATION_DOCUMENTS_CHANGED_EVENT,
  getApplicationDocuments,
  loadApplicationDocumentPages,
  type ApplicationDocument,
  type ApplicationDocumentsView
} from "@/modules/documents/api";
import { ApplicationDocumentLibrary } from "./ApplicationDocumentLibrary";
import { useAuth } from "@/modules/auth/AuthProvider";

const BASE_DOCUMENT_WIDTH = 720;

export function DocumentWorkspace({
  applicationId,
  left,
  right,
  leftTitle = "当前任务",
  rightTitle = "AI参考与证据",
  activeEvidence
}: {
  applicationId: string;
  left?: ReactNode;
  right: ReactNode;
  leftTitle?: string;
  rightTitle?: string;
  activeEvidence?: { evidenceId: string; rawText?: string; sourceLineStart?: number; sourceLineEnd?: number };
}) {
  const { token } = useAuth();
  const [leftVisible, setLeftVisible] = useState(Boolean(left));
  const [rightVisible, setRightVisible] = useState(true);
  const [zoom, setZoom] = useState(92);
  const [compact, setCompact] = useState(() => typeof window !== "undefined" && window.innerWidth < 1024);
  const [view, setView] = useState<ApplicationDocumentsView>();
  const [activeId, setActiveId] = useState<string>();
  const [openIds, setOpenIds] = useState<string[]>([]);
  const [loadError, setLoadError] = useState("");
  const [libraryOpen, setLibraryOpen] = useState(false);
  const documentWidth = Math.round(BASE_DOCUMENT_WIDTH * (zoom / 100));

  const loadDocuments = useCallback(async (preferredId?: string) => {
    if (!token) return;
    setLoadError("");
    try {
      const result = await getApplicationDocuments(token, applicationId);
      setView(result);
      const existing = new Set(result.documents.map((item) => item.documentId));
      const primaryId = result.documents.find((item) => item.primary)?.documentId;
      setOpenIds((current) => {
        let candidateIds = current;
        if (candidateIds.length === 0) {
          try {
            const stored = JSON.parse(sessionStorage.getItem(`application-document-tabs:${applicationId}`) ?? "[]");
            if (Array.isArray(stored)) candidateIds = stored.filter((item): item is string => typeof item === "string");
          } catch {
            candidateIds = [];
          }
        }
        const next = candidateIds.filter((item) => existing.has(item));
        if (primaryId && !next.includes(primaryId)) next.unshift(primaryId);
        if (preferredId && existing.has(preferredId) && !next.includes(preferredId)) next.push(preferredId);
        if (next.length === 0 && result.documents[0]) next.push(result.documents[0].documentId);
        return next;
      });
      setActiveId((current) => {
        const requested = preferredId ?? current;
        if (requested && result.documents.some((item) => item.documentId === requested)) return requested;
        return result.documents.find((item) => item.primary)?.documentId ?? result.documents[0]?.documentId;
      });
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : "工作台资料加载失败");
    }
  }, [applicationId, token]);

  useEffect(() => {
    void loadDocuments();
  }, [loadDocuments]);

  useEffect(() => {
    sessionStorage.setItem(`application-document-tabs:${applicationId}`, JSON.stringify(openIds));
  }, [applicationId, openIds]);

  useEffect(() => {
    const refresh = (event: Event) => {
      const detail = (event as CustomEvent<{ applicationId?: string }>).detail;
      if (detail?.applicationId === applicationId) void loadDocuments();
    };
    window.addEventListener(APPLICATION_DOCUMENTS_CHANGED_EVENT, refresh);
    return () => window.removeEventListener(APPLICATION_DOCUMENTS_CHANGED_EVENT, refresh);
  }, [applicationId, loadDocuments]);

  useEffect(() => {
    const update = () => setCompact(window.innerWidth < 1024);
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, []);

  useEffect(() => {
    if (!activeEvidence || !view) return;
    const resume = view.documents.find((item) => item.primary);
    if (resume) {
      setOpenIds((current) => current.includes(resume.documentId) ? current : [resume.documentId, ...current]);
      setActiveId(resume.documentId);
    }
  }, [activeEvidence?.evidenceId, view]);

  const activeDocument = useMemo(
    () => view?.documents.find((item) => item.documentId === activeId),
    [activeId, view]
  );
  const openDocuments = useMemo(
    () => (view?.documents ?? []).filter((item) => openIds.includes(item.documentId)),
    [openIds, view]
  );

  function changeZoom(delta: number) {
    setZoom((current) => Math.min(135, Math.max(65, current + delta)));
  }

  return (
    <section
      className={`grid gap-4 ${compact ? "h-auto min-h-0 overflow-visible" : "h-[calc(100vh-7.25rem)] min-h-[650px] overflow-hidden"}`}
      style={{
        gridTemplateColumns: compact
          ? "minmax(0, 1fr)"
          : left
          ? `${leftVisible ? "320px" : "44px"} minmax(520px, 1fr) ${rightVisible ? "390px" : "44px"}`
          : `minmax(520px, 1fr) ${rightVisible ? "440px" : "44px"}`
      }}
    >
      {left && leftVisible ? (
        <WorkspacePanel title={leftTitle} side="left" onCollapse={() => setLeftVisible(false)}>{left}</WorkspacePanel>
      ) : left ? (
        <CollapsedPanelButton side="left" title={leftTitle} onClick={() => setLeftVisible(true)} />
      ) : null}

      <div className={`relative min-w-0 overflow-hidden rounded-md border border-line bg-slate-200 shadow-inner ${compact ? "h-[70vh] min-h-[480px]" : ""}`}>
        <div className="flex h-11 items-stretch border-b border-slate-300 bg-white/95">
          <div className="flex min-w-0 flex-1 items-end overflow-x-auto px-2 pt-1">
            {openDocuments.map((document) => (
              <div
                key={document.documentId}
                title={`${document.displayName} · ${document.filename}`}
                className={`group flex h-9 max-w-52 shrink-0 items-center gap-1.5 rounded-t-md border px-2 text-xs font-semibold transition ${activeId === document.documentId ? "border-slate-300 border-b-white bg-white text-ink" : "border-transparent text-slate-600 hover:bg-slate-100"}`}
              >
                <button type="button" onClick={() => setActiveId(document.documentId)} className="flex min-w-0 flex-1 items-center gap-1.5 py-2">
                  <FileText size={14} className={document.scope === "job" ? "shrink-0 text-violet-600" : "shrink-0 text-blue-600"} />
                  <span className="truncate">{document.displayName}</span>
                  {document.scope === "job" ? <span className="rounded bg-violet-50 px-1 text-[10px] text-violet-700">岗位</span> : null}
                </button>
                {!document.primary ? (
                  <button type="button" aria-label={`关闭${document.displayName}页签`} onClick={() => {
                    setOpenIds((current) => current.filter((item) => item !== document.documentId));
                    if (activeId === document.documentId) {
                      const remaining = openDocuments.filter((item) => item.documentId !== document.documentId);
                      setActiveId(remaining[remaining.length - 1]?.documentId);
                    }
                  }} className="rounded p-0.5 text-slate-400 opacity-0 hover:bg-slate-200 hover:text-slate-700 group-hover:opacity-100"><X size={12} /></button>
                ) : null}
              </div>
            ))}
            {view ? (
              <button
                type="button"
                title="打开或上传资料"
                aria-label="打开或上传资料"
                onClick={() => setLibraryOpen(true)}
                className="mb-0.5 ml-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-slate-600 transition hover:bg-slate-100 hover:text-blue-700"
              >
                <Plus size={17} />
              </button>
            ) : null}
          </div>

          <div className="flex shrink-0 items-center gap-1 border-l border-slate-200 px-2">
            <div className="ml-1 inline-flex overflow-hidden rounded-md border border-line bg-white shadow-sm">
              <ToolButton label="缩小文档" onClick={() => changeZoom(-8)}><ZoomOut size={15} /></ToolButton>
              <div className="flex h-8 w-12 items-center justify-center text-xs font-semibold text-slate-700">{zoom}%</div>
              <ToolButton label="放大文档" onClick={() => changeZoom(8)}><ZoomIn size={15} /></ToolButton>
              <ToolButton label="重置缩放" onClick={() => setZoom(92)}><RotateCcw size={14} /></ToolButton>
            </div>
          </div>
        </div>

        <div className="h-[calc(100%-2.75rem)] overflow-auto px-6 py-5">
          <div className="mx-auto flex flex-col items-center gap-6" style={{ width: compact ? "100%" : documentWidth }}>
            {activeDocument && token ? (
              <DocumentPageImages key={activeDocument.documentId} document={activeDocument} token={token} highlighted={Boolean(activeEvidence && activeDocument.primary)} />
            ) : (
              <EmptyDocuments loading={!view && !loadError} error={loadError} canOpen={Boolean(view)} onOpen={() => setLibraryOpen(true)} onRetry={() => void loadDocuments()} />
            )}
          </div>
        </div>

        {activeEvidence ? (
          <div
            role="dialog"
            aria-label="原文依据"
            className="absolute left-4 right-4 top-14 z-10 max-h-44 overflow-auto rounded-md border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-950 shadow-lg"
          >
            {/* evidenceId 是内部追溯键，不能作为业务文案暴露给用户。 */}
            <div className="font-semibold">原文依据</div>
            {activeEvidence.rawText ? (
              <blockquote className="mt-2 whitespace-pre-wrap border-l-2 border-amber-500 pl-3 leading-6">
                {activeEvidence.rawText}
              </blockquote>
            ) : (
              <div className="mt-2 leading-6">未找到可读的原文句子，请检查该依据的来源记录。</div>
            )}
            {activeEvidence.sourceLineStart ? (
              <div className="mt-2 text-xs text-amber-800">
                来源位置：第 {activeEvidence.sourceLineStart}
                {activeEvidence.sourceLineEnd && activeEvidence.sourceLineEnd !== activeEvidence.sourceLineStart
                  ? `-${activeEvidence.sourceLineEnd}`
                  : ""} 行
              </div>
            ) : null}
          </div>
        ) : null}
        {loadError && activeDocument ? <div className="absolute bottom-3 left-3 right-3 z-20 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">{loadError}</div> : null}
      </div>

      {rightVisible ? (
        <WorkspacePanel title={rightTitle} side="right" onCollapse={() => setRightVisible(false)}>{right}</WorkspacePanel>
      ) : (
        <CollapsedPanelButton side="right" title={rightTitle} onClick={() => setRightVisible(true)} />
      )}

      <ApplicationDocumentLibrary
        open={libraryOpen}
        applicationId={applicationId}
        mode="picker"
        openDocumentIds={openIds}
        onClose={() => setLibraryOpen(false)}
        onOpenDocument={(document) => {
          setOpenIds((current) => current.includes(document.documentId) ? current : [...current, document.documentId]);
          setActiveId(document.documentId);
        }}
      />
    </section>
  );
}

function DocumentPageImages({ document, token, highlighted }: { document: ApplicationDocument; token: string; highlighted: boolean }) {
  const [pages, setPages] = useState<string[]>([]);
  const [status, setStatus] = useState<"loading" | "ready" | "failed">("loading");
  const [retryVersion, setRetryVersion] = useState(0);

  useEffect(() => {
    let cancelled = false;
    let ownedPages: string[] = [];
    setPages([]);
    setStatus("loading");
    loadApplicationDocumentPages(token, document)
      .then((nextPages) => {
        ownedPages = nextPages;
        if (cancelled) {
          ownedPages.forEach((page) => URL.revokeObjectURL(page));
          ownedPages = [];
          return;
        }
        setPages(nextPages);
        setStatus("ready");
      })
      .catch(() => { if (!cancelled) setStatus("failed"); });
    return () => {
      cancelled = true;
      ownedPages.forEach((page) => URL.revokeObjectURL(page));
      ownedPages = [];
    };
  }, [document.documentId, retryVersion, token]);

  return (
    <div className="w-full space-y-6">
      {status === "loading" ? <DocumentMessage>正在生成“{document.displayName}”的预览...</DocumentMessage> : null}
      {status === "failed" ? (
        <DocumentMessage tone="error">
          <div>资料图片加载失败。</div>
          <button type="button" className="mt-3 rounded-md border border-rose-200 px-3 py-1.5 text-xs font-semibold hover:bg-rose-50" onClick={() => setRetryVersion((value) => value + 1)}>重新加载</button>
        </DocumentMessage>
      ) : null}
      {pages.map((page, index) => (
        <img key={page} src={page} alt={`${document.displayName}第 ${index + 1} 页`} className={`w-full rounded-sm bg-white shadow-lg ring-2 ${highlighted ? "ring-amber-400" : "ring-slate-300"}`} draggable={false} />
      ))}
    </div>
  );
}

function EmptyDocuments({ loading, error, canOpen, onOpen, onRetry }: { loading: boolean; error: string; canOpen: boolean; onOpen: () => void; onRetry: () => void }) {
  return (
    <DocumentMessage tone={error ? "error" : "default"}>
      <div>{loading ? "正在加载工作台资料..." : error || "当前工作台还没有可查看的文件。"}</div>
      {error ? <button type="button" className="mt-3 rounded-md border px-3 py-1.5 text-xs font-semibold" onClick={onRetry}>重新加载</button> : null}
      {!loading && !error && canOpen ? <button type="button" className="mt-3 rounded-md bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white" onClick={onOpen}>打开文件资料</button> : null}
    </DocumentMessage>
  );
}

function DocumentMessage({ children, tone = "default" }: { children: ReactNode; tone?: "default" | "error" }) {
  return <div className={`rounded-sm bg-white p-8 text-center text-sm shadow-lg ring-1 ${tone === "error" ? "text-rose-700 ring-rose-200" : "text-muted ring-slate-300"}`}>{children}</div>;
}

function ToolButton({ label, onClick, children }: { label: string; onClick: () => void; children: ReactNode }) {
  return <button type="button" aria-label={label} title={label} onClick={onClick} className="flex h-8 w-9 items-center justify-center border-l border-line text-slate-700 first:border-l-0 hover:bg-slate-50">{children}</button>;
}

function WorkspacePanel({ title, side, children, onCollapse }: { title: string; side: "left" | "right"; children: ReactNode; onCollapse: () => void }) {
  return (
    <aside className="flex min-w-0 flex-col overflow-hidden rounded-md border border-line bg-white shadow-sm">
      <div className="flex h-11 items-center justify-between border-b border-line px-3"><div className="truncate text-sm font-semibold text-ink">{title}</div><button className="flex h-8 w-8 items-center justify-center rounded-md text-slate-600 hover:bg-slate-100" type="button" aria-label={`收起${title}`} onClick={onCollapse}><X size={15} /></button></div>
      <div className="flex-1 overflow-y-auto p-3"><div className="space-y-4">{children}</div></div>
      <div className={`h-1 ${side === "left" ? "bg-blue-500" : "bg-cyan-500"}`} />
    </aside>
  );
}

function CollapsedPanelButton({ side, title, onClick }: { side: "left" | "right"; title: string; onClick: () => void }) {
  const Icon = side === "left" ? PanelLeftOpen : PanelRightOpen;
  return <button className="flex h-full items-start justify-center rounded-md border border-line bg-white px-2 py-3 text-slate-700 shadow-sm hover:bg-slate-50" type="button" aria-label={`展开${title}`} onClick={onClick} title={title}><Icon size={18} /></button>;
}
