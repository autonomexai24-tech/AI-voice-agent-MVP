from __future__ import annotations

from dataclasses import dataclass

from voice_agent.config import BusinessConfig
from voice_agent.language import SessionLanguageSnapshot
from voice_agent.logging_config import get_logger, log_event
from voice_agent.prompts.booking import BookingMemoryView, booking_prompt
from voice_agent.prompts.business import business_memory_from_config, business_prompt
from voice_agent.prompts.identity import identity_prompt
from voice_agent.prompts.interruptions import interruptions_prompt
from voice_agent.prompts.multilingual import multilingual_prompt
from voice_agent.prompts.refusal import refusal_prompt

logger = get_logger(__name__)


@dataclass(frozen=True)
class PromptContext:
    business: BusinessConfig
    language: SessionLanguageSnapshot | None = None
    booking_memory: BookingMemoryView | None = None


@dataclass(frozen=True)
class ComposedPrompt:
    instructions: str
    business_name: str
    business_type: str
    services_count: int
    prompt_chars: int


def compose_prompt(context: PromptContext) -> ComposedPrompt:
    business_memory = business_memory_from_config(context.business)
    sections = (
        identity_prompt(),
        business_prompt(business_memory),
        refusal_prompt(),
        booking_prompt(context.booking_memory),
        multilingual_prompt(context.language),
        interruptions_prompt(),
    )
    instructions = "\n\n".join(section for section in sections if section.strip())
    composed = ComposedPrompt(
        instructions=instructions,
        business_name=business_memory.name,
        business_type=business_memory.business_type,
        services_count=len(business_memory.services),
        prompt_chars=len(instructions),
    )
    log_event(
        logger,
        "prompt_composed",
        business_name=composed.business_name,
        business_type=composed.business_type,
        services_count=composed.services_count,
        prompt_chars=composed.prompt_chars,
        active_language=(
            context.language.active_language if context.language is not None else None
        ),
        language_generation=(
            context.language.generation if context.language is not None else None
        ),
        pending_booking_fields=(
            list(context.booking_memory.pending_booking_fields)
            if context.booking_memory is not None
            else None
        ),
    )
    return composed
