import Link from "next/link";

import { formatValue, titleCase } from "@/lib/format";
import type { BookingRecord } from "@/types/api";
import { EmptyState } from "@/components/ui/EmptyState";
import { StatusBadge } from "@/components/ui/StatusBadge";

export function BookingTable({ bookings }: { bookings: BookingRecord[] }) {
  if (!bookings.length) {
    return (
      <EmptyState
        title="No bookings yet"
        detail="No persisted booking records are available."
      />
    );
  }

  return (
    <div className="overflow-x-auto rounded-md border border-[var(--line)] bg-white">
      <table className="w-full min-w-[1040px] border-collapse text-left text-sm">
        <thead className="bg-[var(--panel)] text-xs font-semibold uppercase tracking-[0.12em] text-ink-600">
          <tr>
            <th className="px-4 py-3">Customer</th>
            <th className="px-4 py-3">Service</th>
            <th className="px-4 py-3">Date</th>
            <th className="px-4 py-3">Time</th>
            <th className="px-4 py-3">Doctor</th>
            <th className="px-4 py-3">Status</th>
            <th className="px-4 py-3">Booking ID</th>
          </tr>
        </thead>
        <tbody>
          {bookings.map((booking) => (
            <tr key={booking.booking_id} className="shadow-table-row">
              <td className="px-4 py-3 font-medium text-ink-950">
                {formatValue(booking.customer_name)}
              </td>
              <td className="px-4 py-3 text-ink-600">
                {titleCase(booking.service_type)}
              </td>
              <td className="px-4 py-3 text-ink-600">
                {formatValue(booking.appointment_date)}
              </td>
              <td className="px-4 py-3 text-ink-600">
                {formatValue(booking.appointment_time)}
              </td>
              <td className="px-4 py-3 text-ink-600">
                {formatValue(booking.doctor_preference)}
              </td>
              <td className="px-4 py-3">
                <StatusBadge value={booking.confirmation_status} />
              </td>
              <td className="px-4 py-3">
                <Link
                  className="font-mono text-xs font-semibold text-[var(--steel)] hover:text-ink-950"
                  href={`/bookings/${booking.booking_id}`}
                >
                  {booking.booking_id}
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
