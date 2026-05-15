from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base
from database.models.calls import utc_now


class BusinessSettingsModel(Base):
    __tablename__ = "business_settings"

    settings_id: Mapped[str] = mapped_column(String(64), primary_key=True, default="default")
    business_name: Mapped[str] = mapped_column(String(160), nullable=False)
    business_type: Mapped[str] = mapped_column(String(80), nullable=False)
    services: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    receptionist_tone: Mapped[str | None] = mapped_column(Text)
    default_language: Mapped[str] = mapped_column(String(32), default="english", nullable=False)
    greeting_prompt: Mapped[str | None] = mapped_column(Text)
    refusal_policy: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )
