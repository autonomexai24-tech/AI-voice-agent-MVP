import { frontendLog, logFrontendApiConnected } from "@/lib/logging";
import { API_BASE_URL, API_TIMEOUT_MS, endpoints } from "@/services/endpoints";
import type {
  ApiErrorBody,
  BookingListResponse,
  BookingRecord,
  BusinessSettings,
  BusinessSettingsPatch,
  CallListResponse,
  CallRecord,
  PageInfo,
  TranscriptEntry,
  TranscriptListResponse
} from "@/types/api";

export class ApiClientError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string,
    readonly details: Record<string, unknown> = {}
  ) {
    super(message);
    this.name = "ApiClientError";
  }
}

export async function listCalls(limit = 50): Promise<CallListResponse> {
  const payload = await request(`${endpoints.calls}?limit=${limit}`);
  const response = normalizeCallListResponse(payload);
  frontendLog("calls_loaded", { count: response.items.length });
  return response;
}

export async function getCall(callId: string): Promise<CallRecord> {
  return normalizeCallRecord(await request(endpoints.call(callId)));
}

export async function listBookings(limit = 50): Promise<BookingListResponse> {
  const payload = await request(`${endpoints.bookings}?limit=${limit}`);
  const response = normalizeBookingListResponse(payload);
  frontendLog("bookings_loaded", { count: response.items.length });
  return response;
}

export async function getBooking(bookingId: string): Promise<BookingRecord> {
  return normalizeBookingRecord(await request(endpoints.booking(bookingId)));
}

export async function getCallTranscripts(
  callId: string
): Promise<TranscriptListResponse> {
  const payload = await request(endpoints.transcripts(callId));
  const response = normalizeTranscriptListResponse(payload);
  frontendLog("transcript_loaded", {
    call_id: callId,
    count: response.items.length
  });
  return response;
}

export async function getBusinessSettings(): Promise<BusinessSettings | null> {
  try {
    return normalizeBusinessSettings(await request(endpoints.businessSettings));
  } catch (error) {
    if (error instanceof ApiClientError && error.status === 404) {
      return null;
    }
    throw error;
  }
}

export async function updateBusinessSettings(
  patch: BusinessSettingsPatch
): Promise<BusinessSettings> {
  const settings = normalizeBusinessSettings(
    await request(endpoints.businessSettings, {
      method: "PATCH",
      body: JSON.stringify(patch)
    })
  );
  frontendLog("frontend_settings_saved", {
    settings_id: settings.settings_id,
    services_count: settings.services.length,
    default_language: settings.default_language
  });
  return settings;
}

async function request(path: string, init: RequestInit = {}): Promise<unknown> {
  const url = `${API_BASE_URL}${path}`;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), API_TIMEOUT_MS);

  try {
    const response = await fetch(url, {
      ...init,
      cache: "no-store",
      signal: controller.signal,
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        ...init.headers
      }
    });

    if (!response.ok) {
      const body = await readErrorBody(response);
      const code = body.error?.code ?? "api_request_failed";
      const message = body.error?.message ?? `Request failed with ${response.status}`;
      frontendLog("frontend_api_failed", {
        path,
        status: response.status,
        code
      });
      throw new ApiClientError(
        message,
        response.status,
        code,
        body.error?.details ?? {}
      );
    }

    const payload = await readJsonBody(response);
    logFrontendApiConnected({
      path,
      status: response.status
    });
    return payload;
  } catch (error) {
    if (error instanceof ApiClientError) {
      if (error.code === "api_malformed_response") {
        frontendLog("frontend_api_failed", {
          path,
          status: error.status,
          code: error.code
        });
      }
      throw error;
    }

    const code = isAbortError(error) ? "api_timeout" : "api_network_error";
    frontendLog("frontend_api_failed", {
      path,
      status: 0,
      code
    });
    throw new ApiClientError(
      code === "api_timeout"
        ? "Backend request timed out. Check that the API is healthy and retry."
        : "Backend is unavailable. Check that FastAPI is running locally.",
      0,
      code,
      { path }
    );
  } finally {
    clearTimeout(timeout);
  }
}

async function readJsonBody(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text.trim()) {
    return null;
  }
  try {
    return JSON.parse(text) as unknown;
  } catch {
    throw new ApiClientError(
      "Backend returned malformed JSON.",
      response.status,
      "api_malformed_response"
    );
  }
}

async function readErrorBody(response: Response): Promise<ApiErrorBody> {
  try {
    const text = await response.text();
    if (!text.trim()) {
      return {};
    }
    const payload = JSON.parse(text) as unknown;
    return isRecord(payload) ? (payload as ApiErrorBody) : {};
  } catch {
    return {};
  }
}

function normalizeCallListResponse(payload: unknown): CallListResponse {
  const record = requireRecord(payload, "Call list response");
  return {
    items: requireArray(record.items, "Call list items").map(normalizeCallRecord),
    page: normalizePageInfo(record.page)
  };
}

function normalizeBookingListResponse(payload: unknown): BookingListResponse {
  const record = requireRecord(payload, "Booking list response");
  return {
    items: requireArray(record.items, "Booking list items").map(normalizeBookingRecord),
    page: normalizePageInfo(record.page)
  };
}

function normalizeTranscriptListResponse(payload: unknown): TranscriptListResponse {
  const record = requireRecord(payload, "Transcript list response");
  return {
    call_id: requireString(record.call_id, "Transcript call_id"),
    items: requireArray(record.items, "Transcript list items").map(
      normalizeTranscriptEntry
    )
  };
}

function normalizeCallRecord(payload: unknown): CallRecord {
  const record = requireRecord(payload, "Call record");
  return {
    call_id: requireString(record.call_id, "Call call_id"),
    room_id: optionalString(record.room_id, "Call room_id"),
    caller_phone: optionalString(record.caller_phone, "Call caller_phone"),
    language: optionalString(record.language, "Call language"),
    started_at: requireString(record.started_at, "Call started_at"),
    ended_at: optionalString(record.ended_at, "Call ended_at"),
    duration_seconds: optionalNumber(record.duration_seconds, "Call duration_seconds"),
    booking_outcome: optionalString(record.booking_outcome, "Call booking_outcome"),
    escalation_triggered: requireBoolean(
      record.escalation_triggered,
      "Call escalation_triggered"
    )
  };
}

function normalizeBookingRecord(payload: unknown): BookingRecord {
  const record = requireRecord(payload, "Booking record");
  return {
    booking_id: requireString(record.booking_id, "Booking booking_id"),
    call_id: requireString(record.call_id, "Booking call_id"),
    customer_name: optionalString(record.customer_name, "Booking customer_name"),
    phone_number: optionalString(record.phone_number, "Booking phone_number"),
    service_type: optionalString(record.service_type, "Booking service_type"),
    appointment_date: optionalString(
      record.appointment_date,
      "Booking appointment_date"
    ),
    appointment_time: optionalString(
      record.appointment_time,
      "Booking appointment_time"
    ),
    doctor_preference: optionalString(
      record.doctor_preference,
      "Booking doctor_preference"
    ),
    notes: optionalString(record.notes, "Booking notes"),
    confirmation_status: requireString(
      record.confirmation_status,
      "Booking confirmation_status"
    )
  };
}

function normalizeTranscriptEntry(payload: unknown): TranscriptEntry {
  const record = requireRecord(payload, "Transcript entry");
  return {
    transcript_id: requireString(record.transcript_id, "Transcript transcript_id"),
    call_id: requireString(record.call_id, "Transcript call_id"),
    speaker: requireString(record.speaker, "Transcript speaker"),
    text: requireString(record.text, "Transcript text"),
    timestamp: requireString(record.timestamp, "Transcript timestamp"),
    language: optionalString(record.language, "Transcript language")
  };
}

function normalizeBusinessSettings(payload: unknown): BusinessSettings {
  const record = requireRecord(payload, "Business settings");
  return {
    settings_id: requireString(record.settings_id, "Settings settings_id"),
    business_name: requireString(record.business_name, "Settings business_name"),
    business_type: requireString(record.business_type, "Settings business_type"),
    services: stringArray(record.services),
    receptionist_tone: optionalString(
      record.receptionist_tone,
      "Settings receptionist_tone"
    ),
    default_language: requireString(
      record.default_language,
      "Settings default_language"
    ),
    greeting_prompt: optionalString(
      record.greeting_prompt,
      "Settings greeting_prompt"
    ),
    refusal_policy: optionalString(record.refusal_policy, "Settings refusal_policy"),
    updated_at: requireString(record.updated_at, "Settings updated_at")
  };
}

function normalizePageInfo(payload: unknown): PageInfo {
  const record = isRecord(payload) ? payload : {};
  return {
    next_cursor: optionalString(record.next_cursor, "Page next_cursor"),
    limit:
      typeof record.limit === "number" && Number.isFinite(record.limit)
        ? record.limit
        : 50
  };
}

function requireRecord(
  value: unknown,
  context: string
): Record<string, unknown> {
  if (isRecord(value)) {
    return value;
  }
  throw malformed(`${context} is malformed.`);
}

function requireArray(value: unknown, context: string): unknown[] {
  if (Array.isArray(value)) {
    return value;
  }
  throw malformed(`${context} are malformed.`);
}

function requireString(value: unknown, context: string): string {
  if (typeof value === "string") {
    return value;
  }
  throw malformed(`${context} must be a string.`);
}

function optionalString(value: unknown, context: string): string | null {
  if (value === null || value === undefined) {
    return null;
  }
  if (typeof value === "string") {
    return value;
  }
  throw malformed(`${context} must be a string or null.`);
}

function optionalNumber(value: unknown, context: string): number | null {
  if (value === null || value === undefined) {
    return null;
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  throw malformed(`${context} must be a number or null.`);
}

function requireBoolean(value: unknown, context: string): boolean {
  if (typeof value === "boolean") {
    return value;
  }
  throw malformed(`${context} must be a boolean.`);
}

function stringArray(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter((item): item is string => typeof item === "string");
}

function malformed(message: string): ApiClientError {
  return new ApiClientError(message, 0, "api_malformed_response");
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isAbortError(error: unknown): boolean {
  return isRecord(error) && error.name === "AbortError";
}
