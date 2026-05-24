from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from database.models.bookings import BookingModel, new_booking_id
from database.repositories.pagination import clamp_limit


@dataclass(frozen=True)
class BookingCreate:
    call_id: str
    booking_id: str | None = None
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
    confirmation_status: str = "pending"


@dataclass(frozen=True)
class BookingList:
    items: list[BookingModel]
    next_cursor: str | None


class BookingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_or_update(self, payload: BookingCreate) -> BookingModel:
        booking_id = payload.booking_id or new_booking_id()
        model = await self._session.get(BookingModel, booking_id)
        if model is None:
            model = BookingModel(booking_id=booking_id, call_id=payload.call_id)
            self._session.add(model)

        for field_name in (
            "customer_name",
            "phone_number",
            "service_type",
            "appointment_date",
            "appointment_time",
            "booking_time",
            "doctor_preference",
            "notes",
            "booking_fingerprint",
            "calcom_uid",
            "external_status",
            "confirmed_at",
            "booking_validation_state",
            "confirmation_status",
        ):
            setattr(model, field_name, getattr(payload, field_name))

        await self._session.flush()
        await self._session.refresh(model)
        return model

    async def get_by_fingerprint(self, fingerprint: str) -> BookingModel | None:
        result = await self._session.execute(
            select(BookingModel).where(BookingModel.booking_fingerprint == fingerprint).limit(1)
        )
        return result.scalar_one_or_none()

    async def reserve_by_fingerprint(self, payload: BookingCreate) -> bool:
        if payload.booking_fingerprint is None:
            raise ValueError("booking_fingerprint is required for reservation")
        existing = await self.get_by_fingerprint(payload.booking_fingerprint)
        if existing is not None:
            return False
        try:
            await self.create_or_update(payload)
        except IntegrityError:
            await self._session.rollback()
            return False
        return True

    async def get(self, booking_id: str) -> BookingModel | None:
        return await self._session.get(BookingModel, booking_id)

    async def list(self, *, limit: int | None = None, cursor: str | None = None) -> BookingList:
        page_limit = clamp_limit(limit)
        statement = select(BookingModel).order_by(BookingModel.booking_id.desc())
        if cursor:
            statement = statement.where(BookingModel.booking_id < cursor)
        result = await self._session.execute(statement.limit(page_limit + 1))
        items = list(result.scalars().all())
        next_cursor = None
        if len(items) > page_limit:
            extra = items.pop()
            next_cursor = extra.booking_id
        return BookingList(items=items, next_cursor=next_cursor)
