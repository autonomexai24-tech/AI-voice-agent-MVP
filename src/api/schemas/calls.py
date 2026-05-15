from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from api.schemas.common import PageInfo


class CallRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    call_id: str
    room_id: str | None = None
    caller_phone: str | None = None
    language: str | None = None
    started_at: datetime
    ended_at: datetime | None = None
    duration_seconds: int | None = None
    booking_outcome: str | None = None
    escalation_triggered: bool


class CallListResponse(BaseModel):
    items: list[CallRead]
    page: PageInfo
