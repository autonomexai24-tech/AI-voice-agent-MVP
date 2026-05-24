from __future__ import annotations

from dataclasses import dataclass

from voice_agent.booking.legacy import BookingCalendar
from voice_agent.booking.runtime import (
    BookingNotificationSink,
    BookingRuntimeResult,
)
from voice_agent.booking.workflow import BookingIntelligenceWorkflow
from voice_agent.config import BusinessConfig, CalComConfig
from voice_agent.language import SessionLanguageSnapshot
from voice_agent.runtime_persistence import RuntimePersistenceSink
from voice_agent.session_memory import CallSessionMemory


@dataclass(frozen=True)
class BookingFlowResult:
    handled: bool
    response_text: str | None
    status: str
    captured_fields: tuple[str, ...] = ()
    corrected_fields: tuple[str, ...] = ()
    pending_fields: tuple[str, ...] = ()
    booking_stage: str = "idle"
    runtime_result: BookingRuntimeResult | None = None


class ConversationalBookingFlow:
    def __init__(
        self,
        business_config: BusinessConfig,
        *,
        persistence_sink: RuntimePersistenceSink | None = None,
        calcom_config: CalComConfig | None = None,
        calendar: BookingCalendar | None = None,
        notification_sink: BookingNotificationSink | None = None,
    ) -> None:
        self._workflow = BookingIntelligenceWorkflow(
            business_config,
            persistence_sink=persistence_sink,
            calcom_config=calcom_config,
            calendar=calendar,
            notification_sink=notification_sink,
        )

    async def aclose(self) -> None:
        await self._workflow.aclose()

    async def handle_turn(
        self,
        transcript: str,
        *,
        memory: CallSessionMemory,
        language: SessionLanguageSnapshot | None = None,
        request_id: str | None = None,
    ) -> BookingFlowResult:
        result = await self._workflow.handle_turn(
            transcript,
            memory=memory,
            language=language,
            request_id=request_id,
        )
        return BookingFlowResult(
            handled=result.handled,
            response_text=result.response_text,
            status=result.status,
            captured_fields=result.captured_fields,
            corrected_fields=result.corrected_fields,
            pending_fields=result.pending_fields,
            booking_stage=result.booking_stage,
            runtime_result=result.runtime_result,
        )
