import { LoaderCircle } from "lucide-react";

export function PageLoading({ label = "页面加载中" }: { label?: string }) {
  return (
    <div className="flex min-h-[240px] items-center justify-center gap-2 text-sm text-muted">
      <LoaderCircle className="animate-spin" size={18} />
      {label}
    </div>
  );
}
