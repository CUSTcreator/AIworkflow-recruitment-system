import { AlertCircle, CheckCircle2, Info, TriangleAlert, X } from "lucide-react";
import { useToast, type ToastTone } from "@/shared/toast/ToastProvider";

const toneClasses: Record<ToastTone, string> = {
  success: "border-emerald-200 bg-emerald-50 text-emerald-800",
  error: "border-rose-200 bg-rose-50 text-rose-800",
  warning: "border-amber-200 bg-amber-50 text-amber-800",
  info: "border-blue-200 bg-blue-50 text-blue-800",
};

const icons = {
  success: CheckCircle2,
  error: AlertCircle,
  warning: TriangleAlert,
  info: Info,
};

export function ToastViewport() {
  const { items, dismiss } = useToast();
  return (
    <div className="pointer-events-none fixed inset-x-0 top-4 z-[100] flex flex-col items-center gap-2 px-4" aria-live="polite">
      {items.map((item) => {
        const Icon = icons[item.tone];
        return (
          <div key={item.id} className={`pointer-events-auto flex w-full max-w-lg items-center gap-3 rounded-lg border px-4 py-3 text-sm shadow-lg ${toneClasses[item.tone]}`}>
            <Icon size={18} className="shrink-0" />
            <span className="flex-1">{item.message}</span>
            <button type="button" className="rounded p-1 hover:bg-black/5" onClick={() => dismiss(item.id)} aria-label="关闭提示"><X size={16} /></button>
          </div>
        );
      })}
    </div>
  );
}
