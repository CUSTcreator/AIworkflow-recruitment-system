export type DocumentBusinessStatus =
  | "pending"
  | "processing"
  | "ready"
  | "needs_review";

export interface DocumentStatusPresentation {
  businessStatus: DocumentBusinessStatus;
  label: "待处理" | "资料处理中" | "资料已就绪" | "需要检查";
  tone: string;
}

const presentations: Record<DocumentBusinessStatus, DocumentStatusPresentation> = {
  pending: {
    businessStatus: "pending",
    label: "待处理",
    tone: "border-slate-200 bg-slate-50 text-slate-600"
  },
  processing: {
    businessStatus: "processing",
    label: "资料处理中",
    tone: "border-blue-200 bg-blue-50 text-blue-700"
  },
  ready: {
    businessStatus: "ready",
    label: "资料已就绪",
    tone: "border-emerald-200 bg-emerald-50 text-emerald-700"
  },
  needs_review: {
    businessStatus: "needs_review",
    label: "需要检查",
    tone: "border-amber-200 bg-amber-50 text-amber-800"
  }
};

const internalStatusGroups: Record<string, DocumentBusinessStatus> = {
  uploaded: "pending",
  queued: "pending",
  parsing: "processing",
  parsed: "processing",
  running: "processing",
  processing: "processing",
  extracting: "processing",
  structured: "ready",
  completed: "ready",
  confirmed: "ready",
  structure_review_required: "needs_review",
  review_required: "needs_review",
  duplicate_blocked: "needs_review",
  partially_confirmed: "needs_review",
  failed: "needs_review"
};

export function getDocumentStatusPresentation(status: string): DocumentStatusPresentation {
  return presentations[internalStatusGroups[status] ?? "needs_review"];
}
