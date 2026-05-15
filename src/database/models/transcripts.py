from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.base import Base
from database.models.calls import utc_now


def new_transcript_id() -> str:
    return f"transcript_{uuid4().hex}"


class TranscriptModel(Base):
    __tablename__ = "transcripts"

    transcript_id: Mapped[str] = mapped_column(
        String(96),
        primary_key=True,
        default=new_transcript_id,
    )
    call_id: Mapped[str] = mapped_column(
        ForeignKey("calls.call_id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    speaker: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )
    language: Mapped[str | None] = mapped_column(String(32), index=True)

    call = relationship("CallModel", back_populates="transcripts")
