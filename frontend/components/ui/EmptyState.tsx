export function EmptyState({
  title,
  detail
}: {
  title: string;
  detail: string;
}) {
  return (
    <div className="rounded-md border border-dashed border-[var(--line-strong)] bg-white p-8 text-center">
      <div className="text-base font-semibold text-ink-950">{title}</div>
      <div className="mt-2 text-sm text-ink-600">{detail}</div>
    </div>
  );
}
