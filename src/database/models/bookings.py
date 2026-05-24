from __future__ import annotations

from uuid import uuid4

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.base import Base


def new_booking_id() -> str:
    return f"booking_{uuid4().hex}"


class BookingModel(Base):
    __tablename__ = "bookings"
    __table_args__ = (
        UniqueConstraint("booking_fingerprint", name="uq_bookings_booking_fingerprint"),
    )

    booking_id: Mapped[str] = mapped_column(
        String(96),
        primary_key=True,
        default=new_booking_id,
    )
    call_id: Mapped[str] = mapped_column(
        ForeignKey("calls.call_id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    customer_name: Mapped[str | None] = mapped_column(String(160))
    phone_number: Mapped[str | None] = mapped_column(String(32), index=True)
    service_type: Mapped[str | None] = mapped_column(String(160), index=True)
    appointment_date: Mapped[str | None] = mapped_column(String(64), index=True)
    appointment_time: Mapped[str | None] = mapped_column(String(64))
    booking_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    doctor_preference: Mapped[str | None] = mapped_column(String(160))
    notes: Mapped[str | None] = mapped_column(Text)
    booking_fingerprint: Mapped[str | None] = mapped_column(String(128), index=True)
    calcom_uid: Mapped[str | None] = mapped_column(String(128), index=True)
    external_status: Mapped[str | None] = mapped_column(String(64), index=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    booking_validation_state: Mapped[str | None] = mapped_column(String(64), index=True)
    confirmation_status: Mapped[str] = mapped_column(
        String(64),
        default="pending",
        nullable=False,
        index=True,
    )

    call = relationship("CallModel", back_populates="bookings")
