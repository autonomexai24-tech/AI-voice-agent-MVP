import Link from "next/link";

import { BookingSummaryCard } from "@/components/bookings/BookingSummaryCard";
import { ApiErrorPanel } from "@/components/ui/ApiErrorPanel";
import { PageHeader } from "@/components/ui/PageHeader";
import { getBooking } from "@/services/api";

export const dynamic = "force-dynamic";

export default async function BookingDetailPage({
  params
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  try {
    const booking = await getBooking(id);

    return (
      <div>
        <PageHeader
          title="Booking detail"
          description="Appointment details persisted after the conversation."
          action={
            <Link className="text-sm font-semibold text-[var(--steel)]" href="/bookings">
              Back to bookings
            </Link>
          }
        />
        <BookingSummaryCard booking={booking} />
      </div>
    );
  } catch (error) {
    return (
      <ApiErrorPanel
        title="Booking unavailable"
        message={error instanceof Error ? error.message : "Could not load booking"}
        retryHref={`/bookings/${id}`}
      />
    );
  }
}
