import Link from "next/link";

import { TranscriptTimeline } from "@/components/calls/TranscriptTimeline";
import { ApiErrorPanel } from "@/components/ui/ApiErrorPanel";
import { DetailGrid } from "@/components/ui/DetailGrid";
import { PageHeader } from "@/components/ui/PageHeader";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { formatDateTime, formatDuration, titleCase } from "@/lib/format";
import { getCall, getCallTranscripts } from "@/services/api";

export const dynamic = "force-dynamic";

export default async function CallDetailPage({
  params
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  try {
    const [call, transcriptResponse] = await Promise.all([
      getCall(id),
      getCallTranscripts(id)
    ]);

    return (
      <div>
        <PageHeader
          title="Call detail"
          description="Call lifecycle and transcript timeline for a single conversation."
          action={
            <Link className="text-sm font-semibold text-[var(--steel)]" href="/calls">
              Back to calls
            </Link>
          }
        />
        <section className="mb-6 rounded-md border border-[var(--line)] bg-[var(--panel)] p-5">
          <div className="mb-5 flex items-start justify-between gap-4">
            <div>
              <div className="font-mono text-xs font-semibold text-[var(--steel)]">
                {call.call_id}
              </div>
              <h2 className="mt-2 text-xl font-semibold text-ink-950">
                {call.caller_phone ?? "Unknown caller"}
              </h2>
              <div className="mt-1 text-sm text-ink-600">
                {titleCase(call.language)}
              </div>
            </div>
            <StatusBadge value={call.booking_outcome} />
          </div>
          <DetailGrid
            items={[
              { label: "Room", value: call.room_id },
              { label: "Started", value: formatDateTime(call.started_at) },
              { label: "Ended", value: formatDateTime(call.ended_at) },
              { label: "Duration", value: formatDuration(call.duration_seconds) },
              {
                label: "Escalation",
                value: call.escalation_triggered ? "Triggered" : "Not triggered"
              },
              { label: "Language", value: titleCase(call.language) }
            ]}
          />
        </section>
        <TranscriptTimeline transcripts={transcriptResponse.items} />
      </div>
    );
  } catch (error) {
    return (
      <ApiErrorPanel
        title="Call detail unavailable"
        message={error instanceof Error ? error.message : "Could not load call detail"}
        retryHref={`/calls/${id}`}
      />
    );
  }
}
