import { BookingTable } from "@/components/bookings/BookingTable";
import { ApiErrorPanel } from "@/components/ui/ApiErrorPanel";
import { PageHeader } from "@/components/ui/PageHeader";
import { listBookings } from "@/services/api";

export const dynamic = "force-dynamic";

export default async function BookingsPage() {
  try {
    const response = await listBookings(100);

    return (
      <div>
        <PageHeader
          title="Bookings"
          description="Appointment records collected by the AI receptionist."
        />
        <BookingTable bookings={response.items} />
      </div>
    );
  } catch (error) {
    return (
      <ApiErrorPanel
        title="Bookings unavailable"
        message={error instanceof Error ? error.message : "Could not load bookings"}
        retryHref="/bookings"
      />
    );
  }
}
