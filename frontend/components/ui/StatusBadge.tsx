import { cx } from "@/lib/classNames";

const statusStyles = {
  confirmed: "bg-[var(--moss-soft)] text-[var(--moss)]",
  complete: "bg-[var(--moss-soft)] text-[var(--moss)]",
  pending: "bg-[var(--warning-soft)] text-[var(--warning)]",
  failed: "bg-[var(--danger-soft)] text-[var(--danger)]",
  unknown: "bg-white text-ink-600"
} as const;

export function StatusBadge({ value }: { value: string | null | undefined }) {
  const normalized = (value ?? "unknown").toLowerCase();
  const style =
    statusStyles[normalized as keyof typeof statusStyles] ?? statusStyles.unknown;
  return (
    <span
      className={cx(
        "inline-flex min-w-20 items-center justify-center rounded-md px-2 py-1 text-xs font-semibold",
        style
      )}
    >
      {value ?? "Unknown"}
    </span>
  );
}
