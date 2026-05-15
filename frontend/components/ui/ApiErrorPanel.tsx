export function ApiErrorPanel({
  title = "Could not load data",
  message,
  retryHref
}: {
  title?: string;
  message: string;
  retryHref?: string;
}) {
  return (
    <div className="rounded-md border border-[var(--danger)] bg-[var(--danger-soft)] p-4 text-[var(--danger)]">
      <div className="text-sm font-semibold">{title}</div>
      <div className="mt-1 max-w-3xl text-sm leading-6">{message}</div>
      {retryHref ? (
        <a
          className="mt-3 inline-flex rounded-md border border-[var(--danger)] bg-white px-3 py-1.5 text-sm font-semibold text-[var(--danger)]"
          href={retryHref}
        >
          Retry
        </a>
      ) : null}
    </div>
  );
}
