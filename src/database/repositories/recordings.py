from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from database.models.calls import utc_now
from database.models.recordings import RecordingMetadataModel, new_recording_id


@dataclass(frozen=True)
class RecordingMetadataCreate:
    call_id: str
    file_path: str
    recording_id: str | None = None
    duration_seconds: int | None = None
    created_at: datetime | None = None


class RecordingMetadataRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, payload: RecordingMetadataCreate) -> RecordingMetadataModel:
        model = RecordingMetadataModel(
            recording_id=payload.recording_id or new_recording_id(),
            call_id=payload.call_id,
            file_path=payload.file_path,
            duration_seconds=payload.duration_seconds,
            created_at=payload.created_at or utc_now(),
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return model
