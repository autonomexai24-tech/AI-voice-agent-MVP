from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from api.schemas.common import PageInfo


class BookingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    booking_id: str
    call_id: str
    customer_name: str | None = None
    phone_number: str | None = None
    service_type: str | None = None
    appointment_date: str | None = None
    appointment_time: str | None = None
    booking_time: datetime | None = None
    doctor_preference: str | None = None
    notes: str | None = None
    booking_fingerprint: str | None = None
    calcom_uid: str | None = None
    external_status: str | None = None
    confirmed_at: datetime | None = None
    booking_validation_state: str | None = None
    confirmation_status: str


class BookingListResponse(BaseModel):
    items: list[BookingRead]
    page: PageInfo
