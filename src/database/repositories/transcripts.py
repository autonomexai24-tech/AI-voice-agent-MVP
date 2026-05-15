from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models.calls import utc_now
from database.models.transcripts import TranscriptModel, new_transcript_id


@dataclass(frozen=True)
class TranscriptCreate:
    call_id: str
    speaker: str
    text: str
    transcript_id: str | None = None
    timestamp: datetime | None = None
    language: str | None = None


class TranscriptRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, payload: TranscriptCreate) -> TranscriptModel:
        model = TranscriptModel(
            transcript_id=payload.transcript_id or new_transcript_id(),
            call_id=payload.call_id,
            speaker=payload.speaker,
            text=payload.text,
            timestamp=payload.timestamp or utc_now(),
            language=payload.language,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return model

    async def list_by_call(self, call_id: str) -> list[TranscriptModel]:
        result = await self._session.execute(
            select(TranscriptModel)
            .where(TranscriptModel.call_id == call_id)
            .order_by(TranscriptModel.timestamp.asc(), TranscriptModel.transcript_id.asc())
        )
        return list(result.scalars().all())
