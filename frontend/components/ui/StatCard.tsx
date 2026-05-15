import { cx } from "@/lib/classNames";

const toneStyles = {
  neutral: "border-[var(--line)] bg-white",
  success: "border-[var(--moss)] bg-[var(--moss-soft)]",
  warning: "border-[var(--warning)] bg-[var(--warning-soft)]",
  info: "border-[var(--steel)] bg-[var(--steel-soft)]"
} as const;

export function StatCard({
  label,
  value,
  detail,
  tone = "neutral"
}: {
  label: string;
  value: string;
  detail: string;
  tone?: keyof typeof toneStyles;
}) {
  return (
    <section className={cx("rounded-md border p-4", toneStyles[tone])}>
      <div className="text-xs font-semibold uppercase tracking-[0.14em] text-ink-600">
        {label}
      </div>
      <div className="mt-3 text-3xl font-semibold tracking-normal text-ink-950">
        {value}
      </div>
      <div className="mt-2 text-sm text-ink-600">{detail}</div>
    </section>
  );
}
