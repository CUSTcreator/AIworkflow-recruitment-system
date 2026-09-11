import { useEffect, useState } from 'react';

import { Button } from "@/shared/ui/Button";

export function InterviewTimeDialog({
  open,
  title,
  description,
  confirmLabel,
  variant = "primary",
  onClose,
  onConfirm
}: {
  open: boolean;
  title: string;
  description?: string;
  confirmLabel: string;
  variant?: "primary" | "danger";
  onClose: () => void;
  onConfirm: (effectiveAt: string) => Promise<void> | void;
}) {
  const [effectiveAt, setEffectiveAt] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (open) {
      setEffectiveAt("");
      setSubmitting(false);
    }
  }, [open]);

  if (!open) return null;

  async function confirm() {
    if (!effectiveAt || submitting) return;
    setSubmitting(true);
    try {
      await onConfirm(new Date(effectiveAt).toISOString());
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="fixed inset-0 z-[80] flex items-center justify-center bg-slate-950/40 p-4" role="dialog" aria-modal="true">
      <div className="w-full max-w-md rounded-lg bg-white p-5 shadow-xl">
        <h2 className="text-lg font-semibold text-ink">{title}</h2>
        {description ? <p className="mt-1 text-sm leading-6 text-muted">{description}</p> : null}
        <div className="mt-5">
          <label className="block space-y-1.5 text-sm">
            <span className="font-medium text-slate-700">实际面试时间</span>
            <input
              className="h-10 w-full rounded-md border border-line bg-white px-3"
              type="datetime-local"
              value={effectiveAt}
              onChange={(event) => setEffectiveAt(event.target.value)}
              required
            />
          </label>
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <Button type="button" onClick={onClose} disabled={submitting}>取消</Button>
          <Button type="button" variant={variant} onClick={() => void confirm()} disabled={!effectiveAt || submitting}>
            {submitting ? "正在提交" : confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}
