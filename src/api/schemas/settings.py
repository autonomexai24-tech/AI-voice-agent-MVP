from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class BusinessSettingsRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    settings_id: str
    business_name: str
    business_type: str
    services: list[str]
    receptionist_tone: str | None = None
    default_language: str
    greeting_prompt: str | None = None
    refusal_policy: str | None = None
    updated_at: datetime


class BusinessSettingsPatch(BaseModel):
    business_name: str | None = None
    business_type: str | None = None
    services: list[str] | None = None
    receptionist_tone: str | None = None
    default_language: str | None = None
    greeting_prompt: str | None = None
    refusal_policy: str | None = None
