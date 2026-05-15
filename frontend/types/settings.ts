import type { BusinessSettings } from "@/types/api";

export const DEFAULT_LANGUAGES = [
  { label: "English", value: "english" },
  { label: "Hindi", value: "hindi" },
  { label: "Kannada", value: "kannada" },
  { label: "Tamil", value: "tamil" },
  { label: "Telugu", value: "telugu" },
  { label: "Malayalam", value: "malayalam" },
  { label: "Marathi", value: "marathi" },
  { label: "Punjabi", value: "punjabi" }
] as const;

export interface SettingsActionState {
  status: "idle" | "success" | "error";
  message: string | null;
  settings: BusinessSettings | null;
}

export function emptyBusinessSettings(): BusinessSettings {
  return {
    settings_id: "default",
    business_name: "",
    business_type: "clinic",
    services: [],
    receptionist_tone: "",
    default_language: "english",
    greeting_prompt: "",
    refusal_policy: "",
    updated_at: new Date(0).toISOString()
  };
}
