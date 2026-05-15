from __future__ import annotations

from fastapi import APIRouter, Depends

from api.dependencies.database import get_transcript_repository
from api.schemas.transcripts import TranscriptListResponse, TranscriptRead
from database.repositories.transcripts import TranscriptRepository

router = APIRouter(prefix="/internal/v1/calls", tags=["transcripts"])


@router.get("/{call_id}/transcripts", response_model=TranscriptListResponse)
async def get_call_transcripts(
    call_id: str,
    repository: TranscriptRepository = Depends(get_transcript_repository),
) -> TranscriptListResponse:
    transcripts = await repository.list_by_call(call_id)
    return TranscriptListResponse(
        call_id=call_id,
        items=[TranscriptRead.model_validate(item) for item in transcripts],
    )
