from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models.calls import CallModel, utc_now
from database.repositories.pagination import clamp_limit, decode_cursor, encode_cursor


@dataclass(frozen=True)
class CallCreate:
    call_id: str
    room_id: str | None = None
    caller_phone: str | None = None
    language: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_seconds: int | None = None
    booking_outcome: str | None = None
    escalation_triggered: bool = False


@dataclass(frozen=True)
class CallList:
    items: list[CallModel]
    next_cursor: str | None


class CallRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_or_update(self, payload: CallCreate) -> CallModel:
        model = await self._session.get(CallModel, payload.call_id)
        if model is None:
            model = CallModel(
                call_id=payload.call_id,
                started_at=payload.started_at or utc_now(),
            )
            self._session.add(model)

        for field_name, value in (
            ("room_id", payload.room_id),
            ("caller_phone", payload.caller_phone),
            ("language", payload.language),
            ("started_at", payload.started_at),
            ("ended_at", payload.ended_at),
            ("duration_seconds", payload.duration_seconds),
            ("booking_outcome", payload.booking_outcome),
        ):
            if value is not None:
                setattr(model, field_name, value)
        model.escalation_triggered = payload.escalation_triggered

        await self._session.flush()
        await self._session.refresh(model)
        return model

    async def get(self, call_id: str) -> CallModel | None:
        return await self._session.get(CallModel, call_id)

    async def list(self, *, limit: int | None = None, cursor: str | None = None) -> CallList:
        page_limit = clamp_limit(limit)
        statement = select(CallModel).order_by(
            CallModel.started_at.desc(),
            CallModel.call_id.desc(),
        )
        decoded = decode_cursor(cursor)
        if decoded is not None:
            cursor_time, cursor_id = decoded
            statement = statement.where(
                or_(
                    CallModel.started_at < cursor_time,
                    and_(
                        CallModel.started_at == cursor_time,
                        CallModel.call_id < cursor_id,
                    ),
                )
            )
        result = await self._session.execute(statement.limit(page_limit + 1))
        items = list(result.scalars().all())
        next_cursor = None
        if len(items) > page_limit:
            extra = items.pop()
            next_cursor = encode_cursor(timestamp=extra.started_at, identifier=extra.call_id)
        return CallList(items=items, next_cursor=next_cursor)
