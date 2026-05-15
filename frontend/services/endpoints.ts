import { logFrontendEnvLoaded } from "@/lib/logging";

export const API_BASE_URL =
  process.env.API_BASE_URL ||
  process.env.NEXT_PUBLIC_API_BASE_URL ||
  "http://127.0.0.1:8000";

export const API_TIMEOUT_MS = parsePositiveInt(
  process.env.FRONTEND_API_TIMEOUT_MS,
  8000
);

export const endpoints = {
  calls: "/internal/v1/calls",
  call: (callId: string) => `/internal/v1/calls/${encodeURIComponent(callId)}`,
  transcripts: (callId: string) =>
    `/internal/v1/calls/${encodeURIComponent(callId)}/transcripts`,
  bookings: "/internal/v1/bookings",
  booking: (bookingId: string) =>
    `/internal/v1/bookings/${encodeURIComponent(bookingId)}`,
  businessSettings: "/internal/v1/settings/business"
} as const;

export function logFrontendEnvironment(): void {
  logFrontendEnvLoaded({
    api_base_url: API_BASE_URL,
    timeout_ms: API_TIMEOUT_MS
  });
}

function parsePositiveInt(value: string | undefined, fallback: number): number {
  if (!value) {
    return fallback;
  }
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}
