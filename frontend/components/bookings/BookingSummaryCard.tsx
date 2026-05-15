import { formatValue, titleCase } from "@/lib/format";
import type { BookingRecord } from "@/types/api";
import { DetailGrid } from "@/components/ui/DetailGrid";
import { StatusBadge } from "@/components/ui/StatusBadge";

export function BookingSummaryCard({ booking }: { booking: BookingRecord }) {
  return (
    <section className="rounded-md border border-[var(--line)] bg-[var(--panel)] p-5">
      <div className="mb-5 flex items-start justify-between gap-4">
        <div>
          <div className="text-xs font-semibold uppercase tracking-[0.14em] text-ink-600">
            Booking
          </div>
          <h2 className="mt-2 text-xl font-semibold text-ink-950">
            {formatValue(booking.customer_name)}
          </h2>
          <div className="mt-1 text-sm text-ink-600">
            {titleCase(booking.service_type)}
          </div>
        </div>
        <StatusBadge value={booking.confirmation_status} />
      </div>
      <DetailGrid
        items={[
          { label: "Phone", value: booking.phone_number },
          { label: "Date", value: booking.appointment_date },
          { label: "Time", value: booking.appointment_time },
          { label: "Doctor", value: booking.doctor_preference },
          { label: "Call ID", value: booking.call_id },
          { label: "Booking ID", value: booking.booking_id }
        ]}
      />
      <div className="mt-3 rounded-md border border-[var(--line)] bg-white p-3">
        <div className="text-xs font-semibold uppercase tracking-[0.12em] text-ink-600">
          Notes
        </div>
        <div className="mt-2 text-sm leading-6 text-ink-950">
          {formatValue(booking.notes)}
        </div>
      </div>
    </section>
  );
}
