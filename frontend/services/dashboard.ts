import { listBookings, listCalls } from "@/services/api";
import type { BookingRecord, CallRecord } from "@/types/api";

export interface DashboardMetric {
  label: string;
  value: string;
  detail: string;
  tone: "neutral" | "success" | "warning" | "info";
}

export interface DashboardData {
  calls: CallRecord[];
  bookings: BookingRecord[];
  metrics: DashboardMetric[];
}

export async function getDashboardData(): Promise<DashboardData> {
  const [callsResponse, bookingsResponse] = await Promise.all([
    listCalls(100),
    listBookings(100)
  ]);
  const calls = callsResponse.items;
  const bookings = bookingsResponse.items;
  const answeredCalls = calls.filter((call) => call.ended_at !== null).length;
  const missedCalls = calls.filter((call) => call.ended_at === null).length;
  const successfulBookings = bookings.filter(
    (booking) => booking.confirmation_status === "confirmed"
  ).length;
  const callsWithDuration = calls.filter(
    (call): call is CallRecord & { duration_seconds: number } =>
      typeof call.duration_seconds === "number"
  );
  const averageDuration = callsWithDuration.length
    ? Math.round(
        callsWithDuration.reduce(
          (total, call) => total + call.duration_seconds,
          0
        ) / callsWithDuration.length
      )
    : 0;

  return {
    calls,
    bookings,
    metrics: [
      {
        label: "Total calls",
        value: String(calls.length),
        detail: "Recent persisted call records",
        tone: "neutral"
      },
      {
        label: "Answered calls",
        value: String(answeredCalls),
        detail: "Calls with completed lifecycle",
        tone: "info"
      },
      {
        label: "Missed calls",
        value: String(missedCalls),
        detail: "Calls still missing end time",
        tone: missedCalls ? "warning" : "neutral"
      },
      {
        label: "Successful bookings",
        value: String(successfulBookings),
        detail: "Confirmed booking records",
        tone: "success"
      },
      {
        label: "Avg duration",
        value: averageDuration ? `${averageDuration}s` : "0s",
        detail: "Based on persisted call duration",
        tone: "neutral"
      }
    ]
  };
}
