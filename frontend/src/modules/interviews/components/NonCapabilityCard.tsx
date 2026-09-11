export interface NonCapabilityItem {
  readonly itemId: string;
  label: string;
  value: string;
  reasonCode: string;
  status: "normal" | "attention" | "note";
  sourceStage: string;
}

export interface NonCapabilityCardView {
  title: string;
  description: string;
  items: NonCapabilityItem[];
}

const colors = {
  normal: "border-emerald-200 bg-emerald-50 text-emerald-800",
  attention: "border-amber-200 bg-amber-50 text-amber-900",
  note: "border-slate-200 bg-slate-50 text-slate-700"
} as const;

export function NonCapabilityCard({
  card
}: {
  card?: NonCapabilityCardView;
}) {
  if (!card?.items?.length) return null;
  return (
    <section className="rounded-lg border border-line bg-white p-4">
      <h3 className="text-sm font-semibold text-ink">{card.title}</h3>
      <p className="mt-1 text-xs text-muted">{card.description}</p>
      <div className="mt-3 flex flex-wrap gap-2">
        {card.items.map((item) => (
          <span
            key={item.itemId}
            className={`inline-flex max-w-full items-center gap-1 rounded-full border px-3 py-1.5 text-xs ${colors[item.status]}`}
            title={item.value}
          >
            <strong>{item.label}</strong>
            <span className="min-w-0 whitespace-normal break-words">{item.value}</span>
          </span>
        ))}
      </div>
    </section>
  );
}
