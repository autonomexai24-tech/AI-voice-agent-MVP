from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.base import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CallModel(Base):
    __tablename__ = "calls"

    call_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    room_id: Mapped[str | None] = mapped_column(String(128), index=True)
    caller_phone: Mapped[str | None] = mapped_column(String(32), index=True)
    language: Mapped[str | None] = mapped_column(String(32), index=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    booking_outcome: Mapped[str | None] = mapped_column(String(64), index=True)
    escalation_triggered: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    bookings = relationship("BookingModel", back_populates="call")
    transcripts = relationship("TranscriptModel", back_populates="call")
    recordings = relationship("RecordingMetadataModel", back_populates="call")
