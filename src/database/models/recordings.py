from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.base import Base
from database.models.calls import utc_now


def new_recording_id() -> str:
    return f"recording_{uuid4().hex}"


class RecordingMetadataModel(Base):
    __tablename__ = "recording_metadata"

    recording_id: Mapped[str] = mapped_column(
        String(96),
        primary_key=True,
        default=new_recording_id,
    )
    call_id: Mapped[str] = mapped_column(
        ForeignKey("calls.call_id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )

    call = relationship("CallModel", back_populates="recordings")
