import { ArrowUpDown, ChevronDown } from 'lucide-react';
import type { SelectHTMLAttributes } from "react";

type SortSelectProps = Omit<SelectHTMLAttributes<HTMLSelectElement>, "className"> & {
  className?: string;
};

export function SortSelect({
  className = "",
  children,
  ...props
}: SortSelectProps) {
  return (
    <label
      className={`relative inline-flex h-9 items-center rounded-md border border-slate-300 bg-slate-50 pl-2.5 pr-1 text-sm transition focus-within:border-blue-400 focus-within:ring-2 focus-within:ring-blue-100 hover:bg-slate-100 ${className}`}
    >
      <ArrowUpDown size={14} className="shrink-0 text-slate-500" />
      <span className="ml-1.5 shrink-0 text-xs font-medium text-slate-500">排序</span>
      <span className="mx-2 h-4 w-px shrink-0 bg-slate-300" aria-hidden="true" />
      <select
        {...props}
        aria-label={props["aria-label"] ?? "排序方式"}
        className="h-full min-w-0 appearance-none bg-transparent pl-0 pr-7 text-center text-sm font-medium text-slate-700 outline-none"
      >
        {children}
      </select>
      <ChevronDown
        size={14}
        className="pointer-events-none absolute right-2.5 text-slate-500"
      />
    </label>
  );
}
