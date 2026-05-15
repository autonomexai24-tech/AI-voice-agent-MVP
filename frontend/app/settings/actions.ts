"use server";

import { updateBusinessSettings } from "@/services/api";
import type { BusinessSettingsPatch } from "@/types/api";
import type { SettingsActionState } from "@/types/settings";

export async function saveBusinessSettings(
  _previousState: SettingsActionState,
  formData: FormData
): Promise<SettingsActionState> {
  const patch: BusinessSettingsPatch = {
    business_name: textField(formData, "business_name"),
    business_type: textField(formData, "business_type"),
    services: textAreaList(formData, "services"),
    receptionist_tone: textField(formData, "receptionist_tone"),
    default_language: textField(formData, "default_language"),
    greeting_prompt: textField(formData, "greeting_prompt"),
    refusal_policy: textField(formData, "refusal_policy")
  };

  try {
    const settings = await updateBusinessSettings(patch);
    return {
      status: "success",
      message: "Settings saved.",
      settings
    };
  } catch (error) {
    return {
      status: "error",
      message: error instanceof Error ? error.message : "Could not save settings.",
      settings: null
    };
  }
}

function textField(formData: FormData, name: string): string {
  return String(formData.get(name) ?? "").trim();
}

function textAreaList(formData: FormData, name: string): string[] {
  return String(formData.get(name) ?? "")
    .split(/\r?\n|,|;/)
    .map((item) => item.trim())
    .filter(Boolean);
}
