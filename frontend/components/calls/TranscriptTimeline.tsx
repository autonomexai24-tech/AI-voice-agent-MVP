import { compareDateTime, formatDateTime, titleCase } from "@/lib/format";
import type { TranscriptEntry } from "@/types/api";
import { EmptyState } from "@/components/ui/EmptyState";

export function TranscriptTimeline({
  transcripts
}: {
  transcripts: TranscriptEntry[];
}) {
  if (!transcripts.length) {
    return (
      <EmptyState
        title="No transcript entries"
        detail="No persisted transcript turns are available."
      />
    );
  }

  const ordered = [...transcripts].sort(
    (a, b) =>
      compareDateTime(a.timestamp, b.timestamp) ||
      a.transcript_id.localeCompare(b.transcript_id)
  );

  return (
    <div className="max-h-[640px] overflow-y-auto rounded-md border border-[var(--line)] bg-white">
      <div className="divide-y divide-[var(--line)]">
        {ordered.map((entry) => (
          <article
            key={entry.transcript_id}
            className="grid grid-cols-[160px_minmax(0,1fr)] gap-4 p-4"
          >
            <div>
              <div className="text-xs font-semibold uppercase tracking-[0.12em] text-ink-600">
                {titleCase(entry.speaker)}
              </div>
              <div className="mt-1 text-xs text-ink-600">
                {formatDateTime(entry.timestamp)}
              </div>
              <div className="mt-2 text-xs text-[var(--steel)]">
                {titleCase(entry.language)}
              </div>
            </div>
            <p className="whitespace-pre-wrap break-words text-[15px] leading-7 text-ink-950">
              {entry.text}
            </p>
          </article>
        ))}
      </div>
    </div>
  );
}
