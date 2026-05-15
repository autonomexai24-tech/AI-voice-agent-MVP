import { formatValue } from "@/lib/format";

export function DetailGrid({
  items
}: {
  items: Array<{ label: string; value: string | null | undefined }>;
}) {
  return (
    <div className="grid grid-cols-3 gap-3">
      {items.map((item) => (
        <div
          key={item.label}
          className="rounded-md border border-[var(--line)] bg-white p-3"
        >
          <div className="text-xs font-semibold uppercase tracking-[0.12em] text-ink-600">
            {item.label}
          </div>
          <div className="mt-2 text-sm font-medium text-ink-950">
            {formatValue(item.value)}
          </div>
        </div>
      ))}
    </div>
  );
}
