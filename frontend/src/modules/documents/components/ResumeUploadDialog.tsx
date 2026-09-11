import {
  AlertCircle,
  CheckCircle2,
  FileText,
  LoaderCircle,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import {
  useState,
  type DragEvent as ReactDragEvent,
  type FormEvent,
} from "react";

import { uploadResumeDocument } from "@/modules/documents/resumeApi";
import { useAuth } from "@/modules/auth/AuthProvider";
import { isApiError } from "@/shared/api/httpClient";
import { useToast } from "@/shared/toast/ToastProvider";
import { Button } from "@/shared/ui/Button";

const MAX_RESUME_COUNT = 20;
const MAX_FILE_SIZE = 20 * 1024 * 1024;
const MAX_BATCH_SIZE = 200 * 1024 * 1024;
const UPLOAD_CONCURRENCY = 3;

type UploadStatus =
  | "ready"
  | "uploading"
  | "accepted"
  | "duplicate"
  | "failed";

type ResumeUploadItem = {
  id: string;
  file: File;
  relativePath: string;
  idempotencyKey: string;
  status: UploadStatus;
  error?: string;
  retryable?: boolean;
};

type CollectedFile = {
  file: File;
  relativePath: string;
};

function formatFileSize(size: number): string {
  if (size < 1024 * 1024) return `${Math.ceil(size / 1024)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function fileIdentity(item: CollectedFile): string {
  return [
    item.relativePath.toLocaleLowerCase(),
    item.file.size,
    item.file.lastModified,
  ].join(":");
}

function readFileEntry(entry: FileSystemFileEntry): Promise<CollectedFile> {
  return new Promise((resolve, reject) => {
    entry.file(
      (file) =>
        resolve({
          file,
          relativePath: entry.fullPath.replace(/^\//, "") || file.name,
        }),
      reject,
    );
  });
}

function readDirectoryEntries(
  reader: FileSystemDirectoryReader,
): Promise<FileSystemEntry[]> {
  return new Promise((resolve, reject) => {
    const entries: FileSystemEntry[] = [];
    const readNext = () => {
      reader.readEntries((batch) => {
        if (batch.length === 0) {
          resolve(entries);
          return;
        }
        entries.push(...batch);
        readNext();
      }, reject);
    };
    readNext();
  });
}

async function collectEntryFiles(
  entry: FileSystemEntry,
): Promise<CollectedFile[]> {
  if (entry.isFile) {
    return [await readFileEntry(entry as FileSystemFileEntry)];
  }
  if (!entry.isDirectory) return [];

  const children = await readDirectoryEntries(
    (entry as FileSystemDirectoryEntry).createReader(),
  );
  const files: CollectedFile[] = [];
  for (const child of children) {
    files.push(...(await collectEntryFiles(child)));
  }
  return files;
}

async function collectDroppedFiles(
  dataTransfer: DataTransfer,
): Promise<CollectedFile[]> {
  const entries = Array.from(dataTransfer.items)
    .filter((item) => item.kind === "file")
    .map((item) => item.webkitGetAsEntry())
    .filter((entry): entry is FileSystemEntry => entry !== null);

  if (entries.length > 0) {
    const files: CollectedFile[] = [];
    for (const entry of entries) {
      files.push(...(await collectEntryFiles(entry)));
    }
    return files;
  }

  return Array.from(dataTransfer.files).map((file) => ({
    file,
    relativePath: file.webkitRelativePath || file.name,
  }));
}

function isDuplicateDocumentError(reason: unknown): boolean {
  return (
    isApiError(reason) &&
    (reason.code === "duplicate_document" ||
      reason.context?.businessCode === "duplicate_document")
  );
}

async function runWithConcurrency<T>(
  values: T[],
  limit: number,
  task: (value: T) => Promise<void>,
): Promise<void> {
  let nextIndex = 0;
  async function runner() {
    while (nextIndex < values.length) {
      const value = values[nextIndex];
      nextIndex += 1;
      await task(value);
    }
  }

  await Promise.all(
    Array.from(
      { length: Math.min(limit, values.length) },
      () => runner(),
    ),
  );
}

const statusView: Record<
  UploadStatus,
  { label: string; className: string }
> = {
  ready: { label: "待上传", className: "text-slate-500" },
  uploading: { label: "上传中", className: "text-blue-700" },
  accepted: { label: "已提交", className: "text-emerald-700" },
  duplicate: { label: "已存在", className: "text-amber-700" },
  failed: { label: "上传失败", className: "text-rose-700" },
};

export function ResumeUploadDialog({
  open,
  onClose,
  onUploaded,
}: {
  open: boolean;
  onClose: () => void;
  onUploaded: () => void;
}) {
  const { token } = useAuth();
  const toast = useToast();
  const [items, setItems] = useState<ResumeUploadItem[]>([]);
  const [uploading, setUploading] = useState(false);
  const [dragActive, setDragActive] = useState(false);
  const [notice, setNotice] = useState("");
  const [resultMessage, setResultMessage] = useState("");
  const [uploadProgress, setUploadProgress] = useState({ completed: 0, total: 0 });

  if (!open) return null;

  const retryItems = items.filter(
    (item) => item.status === "ready" || (item.status === "failed" && item.retryable),
  );
  const hasResults = items.some((item) =>
    ["accepted", "duplicate", "failed"].includes(item.status),
  );

  function closeDialog() {
    if (uploading) return;
    setItems([]);
    setNotice("");
    setResultMessage("");
    setDragActive(false);
    setUploadProgress({ completed: 0, total: 0 });
    onClose();
  }

  function addFiles(collected: CollectedFile[]) {
    const nextItems = [...items];
    const identities = new Set(
      nextItems.map((item) =>
        fileIdentity({ file: item.file, relativePath: item.relativePath }),
      ),
    );
    let totalSize = nextItems.reduce((sum, item) => sum + item.file.size, 0);
    let nonPdfCount = 0;
    let oversizedCount = 0;
    let longNameCount = 0;
    let duplicateCount = 0;
    let overCount = 0;
    let overBatchSizeCount = 0;

    for (const collectedFile of collected) {
      const { file } = collectedFile;
      if (!file.name.toLocaleLowerCase().endsWith(".pdf")) {
        nonPdfCount += 1;
        continue;
      }
      if (file.name.length > 128) {
        longNameCount += 1;
        continue;
      }
      if (file.size > MAX_FILE_SIZE) {
        oversizedCount += 1;
        continue;
      }
      const identity = fileIdentity(collectedFile);
      if (identities.has(identity)) {
        duplicateCount += 1;
        continue;
      }
      if (nextItems.length >= MAX_RESUME_COUNT) {
        overCount += 1;
        continue;
      }
      if (totalSize + file.size > MAX_BATCH_SIZE) {
        overBatchSizeCount += 1;
        continue;
      }

      identities.add(identity);
      totalSize += file.size;
      nextItems.push({
        id: crypto.randomUUID(),
        file,
        relativePath: collectedFile.relativePath || file.name,
        idempotencyKey: crypto.randomUUID(),
        status: "ready",
      });
    }

    const messages = [
      nonPdfCount ? `已忽略${nonPdfCount}个非PDF文件` : "",
      oversizedCount ? `已忽略${oversizedCount}个超过20 MB的文件` : "",
      longNameCount ? `已忽略${longNameCount}个文件名超过128字符的文件` : "",
      duplicateCount ? `已忽略${duplicateCount}个重复选择的文件` : "",
      overCount ? `单次最多上传${MAX_RESUME_COUNT}份，另有${overCount}份未加入` : "",
      overBatchSizeCount ? `批次总大小最多200 MB，另有${overBatchSizeCount}份未加入` : "",
    ].filter(Boolean);

    setItems(nextItems);
    setNotice(messages.join("；"));
    setResultMessage("");
  }

  async function handleDrop(event: ReactDragEvent<HTMLLabelElement>) {
    event.preventDefault();
    if (uploading) return;
    setDragActive(false);
    try {
      const droppedFiles = await collectDroppedFiles(event.dataTransfer);
      if (droppedFiles.length === 0) {
        setNotice("没有从拖入内容中读取到文件。");
        return;
      }
      addFiles(droppedFiles);
    } catch {
      setNotice("文件夹读取失败，请重新拖入或直接选择PDF文件。");
    }
  }

  function updateItem(id: string, patch: Partial<ResumeUploadItem>) {
    setItems((current) =>
      current.map((item) => (item.id === id ? { ...item, ...patch } : item)),
    );
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!token || retryItems.length === 0) return;

    setUploading(true);
    setNotice("");
    setResultMessage("");
    setUploadProgress({ completed: 0, total: retryItems.length });
    let successCount = 0;
    let duplicateCount = 0;
    let failureCount = 0;

    await runWithConcurrency(retryItems, UPLOAD_CONCURRENCY, async (item) => {
      updateItem(item.id, { status: "uploading", error: undefined });
      try {
        await uploadResumeDocument(token, {
          file: item.file,
          idempotencyKey: item.idempotencyKey,
        });
        successCount += 1;
        updateItem(item.id, {
          status: "accepted",
          error: undefined,
          retryable: false,
        });
      } catch (reason) {
        if (isDuplicateDocumentError(reason)) {
          duplicateCount += 1;
          updateItem(item.id, {
            status: "duplicate",
            error: "该简历文件已上传过",
            retryable: false,
          });
        } else {
          failureCount += 1;
          updateItem(item.id, {
            status: "failed",
            error: reason instanceof Error ? reason.message : "简历上传失败",
            retryable: isApiError(reason) ? reason.retryable : true,
          });
        }
      } finally {
        setUploadProgress((current) => ({
          ...current,
          completed: current.completed + 1,
        }));
      }
    });

    setUploading(false);
    if (successCount > 0) onUploaded();

    if (duplicateCount > 0 || failureCount > 0) {
      const summary = [
        successCount ? `${successCount}份已提交` : "",
        duplicateCount ? `${duplicateCount}份已存在` : "",
        failureCount ? `${failureCount}份上传失败` : "",
      ].filter(Boolean).join("，");
      setResultMessage(`${summary}。`);
      toast.warning(summary);
      return;
    }

    toast.success(`${successCount}份简历已接收，正在后台解析`);
    setItems([]);
    onClose();
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="resume-upload-title"
    >
      <div className="max-h-[calc(100vh-2rem)] w-full max-w-5xl overflow-y-auto rounded-lg bg-white p-5 shadow-xl">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 id="resume-upload-title" className="text-lg font-semibold text-ink">
              导入候选简历
            </h2>
            <p className="mt-1 text-sm text-muted">
              单次最多20份PDF，单份不超过20 MB。
            </p>
          </div>
          <button
            className="rounded-md p-2 text-muted hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-45"
            type="button"
            onClick={closeDialog}
            disabled={uploading}
            aria-label="关闭"
            title="关闭"
          >
            <X size={18} />
          </button>
        </div>

        <form className="mt-5 space-y-4" onSubmit={submit}>
          <label
            className={`flex min-h-48 flex-col items-center justify-center rounded-md border border-dashed px-4 py-5 text-center transition sm:min-h-80 ${
              uploading
                ? "cursor-not-allowed border-slate-200 bg-slate-50 opacity-60"
                : dragActive
                  ? "cursor-copy border-blue-500 bg-blue-50"
                  : "cursor-pointer border-slate-300 bg-slate-50 hover:bg-slate-100"
            }`}
            data-testid="resume-drop-zone"
            onDragEnter={(event) => {
              event.preventDefault();
              if (!uploading) setDragActive(true);
            }}
            onDragOver={(event) => event.preventDefault()}
            onDragLeave={(event) => {
              if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
                setDragActive(false);
              }
            }}
            onDrop={(event) => void handleDrop(event)}
          >
            <Upload size={26} className={dragActive ? "text-blue-600" : "text-slate-500"} />
            <span className="mt-2 text-sm font-medium text-ink">
              拖放PDF文件或文件夹，或点击选择
            </span>
            <span className="mt-1 text-xs text-muted">
              {items.length > 0
                ? `已加入${items.length}份，共${formatFileSize(items.reduce((sum, item) => sum + item.file.size, 0))}`
                : "文件夹中的非PDF文件会自动忽略"}
            </span>
            <input
              className="sr-only"
              type="file"
              multiple
              accept=".pdf,application/pdf"
              disabled={uploading}
              onChange={(event) => {
                addFiles(
                  Array.from(event.target.files ?? []).map((file) => ({
                    file,
                    relativePath: file.webkitRelativePath || file.name,
                  })),
                );
                event.currentTarget.value = "";
              }}
            />
          </label>

          {notice ? (
            <p className="rounded-md bg-amber-50 p-3 text-sm text-amber-800">
              {notice}
            </p>
          ) : null}

          {items.length > 0 ? (
            <div className="overflow-hidden rounded-md border border-line">
              <div className="flex items-center justify-between border-b border-line bg-slate-50 px-3 py-2 text-xs text-muted">
                <span>{items.length}份简历</span>
                {!uploading && items.every((item) => item.status === "ready") ? (
                  <button
                    type="button"
                    className="inline-flex items-center gap-1 text-slate-600 hover:text-rose-700"
                    onClick={() => {
                      setItems([]);
                      setNotice("");
                    }}
                  >
                    <Trash2 size={13} />
                    清空
                  </button>
                ) : null}
              </div>
              <div className="max-h-64 divide-y divide-slate-100 overflow-y-auto">
                {items.map((item) => {
                  const view = statusView[item.status];
                  return (
                    <div key={item.id} className="flex min-h-12 items-center gap-3 px-3 py-2">
                      {item.status === "uploading" ? (
                        <LoaderCircle size={16} className="shrink-0 animate-spin text-blue-600" />
                      ) : item.status === "accepted" ? (
                        <CheckCircle2 size={16} className="shrink-0 text-emerald-600" />
                      ) : item.status === "failed" || item.status === "duplicate" ? (
                        <AlertCircle size={16} className={`shrink-0 ${view.className}`} />
                      ) : (
                        <FileText size={16} className="shrink-0 text-slate-400" />
                      )}
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm text-slate-700" title={item.relativePath}>
                          {item.relativePath}
                        </p>
                        <p className="mt-0.5 truncate text-xs text-muted">
                          {formatFileSize(item.file.size)}
                          {item.error ? ` · ${item.error}` : ""}
                        </p>
                      </div>
                      <span className={`shrink-0 text-xs font-medium ${view.className}`}>
                        {view.label}
                      </span>
                      {!uploading && item.status !== "accepted" ? (
                        <button
                          type="button"
                          className="shrink-0 rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-rose-700"
                          onClick={() => setItems((current) => current.filter((value) => value.id !== item.id))}
                          aria-label={`移除${item.file.name}`}
                          title="移除"
                        >
                          <X size={14} />
                        </button>
                      ) : (
                        <span className="h-6 w-6 shrink-0" />
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          ) : null}

          {uploading ? (
            <div className="space-y-1.5" aria-live="polite">
              <div className="flex justify-between text-xs text-muted">
                <span>正在提交简历</span>
                <span>{uploadProgress.completed}/{uploadProgress.total}</span>
              </div>
              <div className="h-1.5 overflow-hidden rounded bg-slate-100">
                <div
                  className="h-full bg-blue-600 transition-all"
                  style={{
                    width: `${uploadProgress.total ? (uploadProgress.completed / uploadProgress.total) * 100 : 0}%`,
                  }}
                />
              </div>
            </div>
          ) : null}

          {resultMessage ? (
            <p className="rounded-md bg-amber-50 p-3 text-sm text-amber-800" role="status">
              {resultMessage}
            </p>
          ) : null}

          <div className="flex justify-end gap-2">
            <Button type="button" onClick={closeDialog} disabled={uploading}>
              {hasResults ? "关闭" : "取消"}
            </Button>
            <Button
              type="submit"
              variant="primary"
              disabled={uploading || retryItems.length === 0}
            >
              {uploading ? (
                <LoaderCircle size={16} className="animate-spin" />
              ) : (
                <Upload size={16} />
              )}
              {uploading
                ? `正在上传 ${uploadProgress.completed}/${uploadProgress.total}`
                : hasResults
                  ? `重试${retryItems.length}份`
                  : `确认上传${retryItems.length || ""}份简历`}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
