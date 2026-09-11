import type { ReactNode } from 'react';

export function StatCard({
  label,
  value,
  icon,
  tone = "blue",
  selected = false,
  onClick
}: {
  label: string;
  value: string | number;
  icon?: ReactNode;
  tone?: "blue" | "green" | "amber" | "rose" | "slate";
  selected?: boolean;
  onClick?: () => void;
}) {
  const tones = {
    blue: "bg-blue-50 text-blue-700",
    green: "bg-emerald-50 text-emerald-700",
    amber: "bg-amber-50 text-amber-800",
    rose: "bg-rose-50 text-rose-700",
    slate: "bg-slate-100 text-slate-700"
  };
  const content = (
    <>
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted">{label}</p>
        {icon ? <div className={`rounded-md p-2 ${tones[tone]}`}>{icon}</div> : null}
      </div>
      <div className="mt-3 text-2xl font-semibold text-ink">{value}</div>
    </>
  );
  if (onClick) {
    return (
      <button
        type="button"
        aria-pressed={selected}
        onClick={onClick}
        className={`w-full rounded-md border bg-white p-4 text-left shadow-sm transition hover:border-blue-300 hover:shadow ${selected ? "border-blue-500 ring-2 ring-blue-100" : "border-line"}`}
      >
        {content}
      </button>
    );
  }
  return <div className="rounded-md border border-line bg-white p-4 shadow-sm">{content}</div>;
}
