from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
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
    doctor_preference: str | None = None
    notes: str | None = None
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
            "doctor_preference",
            "notes",
            "confirmation_status",
        ):
            setattr(model, field_name, getattr(payload, field_name))

        await self._session.flush()
        await self._session.refresh(model)
        return model

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
