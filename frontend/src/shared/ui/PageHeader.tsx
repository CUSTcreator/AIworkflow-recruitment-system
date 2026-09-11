import type { ReactNode } from 'react';

export function PageHeader({
  title,
  actions
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="mx-auto mb-3 flex min-h-10 max-w-5xl flex-wrap items-center justify-center gap-3 rounded-md bg-white/80 px-4 py-2 text-center shadow-sm backdrop-blur">
      <h1 className="text-base font-semibold text-ink">{title}</h1>
      {actions ? <div className="flex flex-wrap items-center justify-center gap-2">{actions}</div> : null}
    </div>
  );
}
