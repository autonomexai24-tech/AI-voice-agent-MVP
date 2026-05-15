"use client";

export default function Error({
  error,
  reset
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <div className="rounded-md border border-[var(--danger)] bg-[var(--danger-soft)] p-4 text-[var(--danger)]">
      <div className="text-sm font-semibold">Frontend runtime error</div>
      <div className="mt-1 max-w-3xl text-sm leading-6">
        {error.message || "The page could not render safely."}
      </div>
      <button
        type="button"
        onClick={reset}
        className="mt-3 rounded-md border border-[var(--danger)] bg-white px-3 py-1.5 text-sm font-semibold text-[var(--danger)]"
      >
        Retry
      </button>
    </div>
  );
}
