from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class WorkerHeartbeatModel(Base):
    __tablename__ = "worker_heartbeats"

    worker_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), default="running", nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )
    active_session_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    uptime_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    current_calls: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    memory_usage_mb: Mapped[float | None] = mapped_column(Float)
    cpu_usage_percent: Mapped[float | None] = mapped_column(Float)
