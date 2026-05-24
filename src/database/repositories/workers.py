from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models.workers import WorkerHeartbeatModel, utc_now


@dataclass(frozen=True)
class WorkerHeartbeatCreate:
    worker_id: str
    active_session_count: int
    uptime_seconds: int
    current_calls: tuple[str, ...]
    memory_usage_mb: float | None = None
    cpu_usage_percent: float | None = None
    status: str = "running"
    started_at: datetime | None = None
    last_seen_at: datetime | None = None


class WorkerHeartbeatRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(self, payload: WorkerHeartbeatCreate) -> WorkerHeartbeatModel:
        model = await self._session.get(WorkerHeartbeatModel, payload.worker_id)
        if model is None:
            model = WorkerHeartbeatModel(
                worker_id=payload.worker_id,
                started_at=payload.started_at or utc_now(),
            )
            self._session.add(model)

        model.status = payload.status
        model.last_seen_at = payload.last_seen_at or utc_now()
        model.active_session_count = payload.active_session_count
        model.uptime_seconds = payload.uptime_seconds
        model.current_calls = list(payload.current_calls)
        model.memory_usage_mb = payload.memory_usage_mb
        model.cpu_usage_percent = payload.cpu_usage_percent

        await self._session.flush()
        await self._session.refresh(model)
        return model

    async def list_stale(
        self,
        *,
        cutoff: datetime,
        active_statuses: tuple[str, ...] = ("running", "draining"),
    ) -> list[WorkerHeartbeatModel]:
        statement = select(WorkerHeartbeatModel).where(
            WorkerHeartbeatModel.last_seen_at < cutoff,
            WorkerHeartbeatModel.status.in_(active_statuses),
        )
        result = await self._session.execute(statement)
        return list(result.scalars().all())

    async def mark_stale(self, worker_id: str) -> WorkerHeartbeatModel | None:
        model = await self._session.get(WorkerHeartbeatModel, worker_id)
        if model is None:
            return None
        model.status = "stale"
        await self._session.flush()
        await self._session.refresh(model)
        return model
