export interface PageInfo {
  next_cursor: string | null;
  limit: number;
}

export interface CallRecord {
  call_id: string;
  room_id: string | null;
  caller_phone: string | null;
  language: string | null;
  started_at: string;
  ended_at: string | null;
  duration_seconds: number | null;
  booking_outcome: string | null;
  escalation_triggered: boolean;
}

export interface CallListResponse {
  items: CallRecord[];
  page: PageInfo;
}

export interface BookingRecord {
  booking_id: string;
  call_id: string;
  customer_name: string | null;
  phone_number: string | null;
  service_type: string | null;
  appointment_date: string | null;
  appointment_time: string | null;
  doctor_preference: string | null;
  notes: string | null;
  confirmation_status: string;
}

export interface BookingListResponse {
  items: BookingRecord[];
  page: PageInfo;
}

export interface TranscriptEntry {
  transcript_id: string;
  call_id: string;
  speaker: string;
  text: string;
  timestamp: string;
  language: string | null;
}

export interface TranscriptListResponse {
  call_id: string;
  items: TranscriptEntry[];
}

export interface BusinessSettings {
  settings_id: string;
  business_name: string;
  business_type: string;
  services: string[];
  receptionist_tone: string | null;
  default_language: string;
  greeting_prompt: string | null;
  refusal_policy: string | null;
  updated_at: string;
}

export type BusinessSettingsPatch = Partial<
  Pick<
    BusinessSettings,
    | "business_name"
    | "business_type"
    | "services"
    | "receptionist_tone"
    | "default_language"
    | "greeting_prompt"
    | "refusal_policy"
  >
>;

export interface ApiErrorBody {
  error?: {
    code?: string;
    message?: string;
    details?: Record<string, unknown>;
    request_id?: string | null;
  };
}
