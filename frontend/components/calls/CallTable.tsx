import Link from "next/link";

import { formatDateTime, formatDuration, formatValue, titleCase } from "@/lib/format";
import type { CallRecord } from "@/types/api";
import { EmptyState } from "@/components/ui/EmptyState";
import { StatusBadge } from "@/components/ui/StatusBadge";

export function CallTable({ calls }: { calls: CallRecord[] }) {
  if (!calls.length) {
    return (
      <EmptyState
        title="No calls yet"
        detail="No persisted call records are available."
      />
    );
  }

  return (
    <div className="overflow-x-auto rounded-md border border-[var(--line)] bg-white">
      <table className="w-full min-w-[980px] border-collapse text-left text-sm">
        <thead className="bg-[var(--panel)] text-xs font-semibold uppercase tracking-[0.12em] text-ink-600">
          <tr>
            <th className="px-4 py-3">Caller</th>
            <th className="px-4 py-3">Language</th>
            <th className="px-4 py-3">Duration</th>
            <th className="px-4 py-3">Booking</th>
            <th className="px-4 py-3">Started</th>
            <th className="px-4 py-3">Call ID</th>
          </tr>
        </thead>
        <tbody>
          {calls.map((call) => (
            <tr key={call.call_id} className="shadow-table-row">
              <td className="px-4 py-3 font-medium text-ink-950">
                {formatValue(call.caller_phone)}
              </td>
              <td className="px-4 py-3 text-ink-600">
                {titleCase(call.language)}
              </td>
              <td className="px-4 py-3 text-ink-600">
                {formatDuration(call.duration_seconds)}
              </td>
              <td className="px-4 py-3">
                <StatusBadge value={call.booking_outcome} />
              </td>
              <td className="px-4 py-3 text-ink-600">
                {formatDateTime(call.started_at)}
              </td>
              <td className="px-4 py-3">
                <Link
                  className="font-mono text-xs font-semibold text-[var(--steel)] hover:text-ink-950"
                  href={`/calls/${call.call_id}`}
                >
                  {call.call_id}
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
