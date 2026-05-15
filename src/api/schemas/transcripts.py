from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class TranscriptRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    transcript_id: str
    call_id: str
    speaker: str
    text: str
    timestamp: datetime
    language: str | None = None


class TranscriptListResponse(BaseModel):
    call_id: str
    items: list[TranscriptRead]
