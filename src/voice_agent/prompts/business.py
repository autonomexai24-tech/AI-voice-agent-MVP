from __future__ import annotations

from dataclasses import dataclass

from voice_agent.config import BusinessConfig, BusinessFAQ
from voice_agent.logging_config import get_logger, log_event

logger = get_logger(__name__)


@dataclass(frozen=True)
class BusinessMemory:
    name: str
    business_type: str
    services: tuple[str, ...]
    faqs: tuple[BusinessFAQ, ...]
    receptionist_tone: str
    refusal_behavior: str
    receptionist_personality: str


def business_memory_from_config(
    config: BusinessConfig,
    *,
    emit_log: bool = True,
) -> BusinessMemory:
    memory = BusinessMemory(
        name=config.name,
        business_type=config.business_type,
        services=config.services,
        faqs=config.faqs,
        receptionist_tone=config.receptionist_tone,
        refusal_behavior=config.refusal_behavior,
        receptionist_personality=config.receptionist_personality,
    )
    if emit_log:
        log_event(
            logger,
            "business_memory_loaded",
            business_name=memory.name,
            business_type=memory.business_type,
            services_count=len(memory.services),
            services=list(memory.services),
            faqs_count=len(memory.faqs),
            receptionist_tone=memory.receptionist_tone,
        )
    return memory


def business_prompt(memory: BusinessMemory) -> str:
    return (
        "Business context:\n"
        f"- Business name: {memory.name}\n"
        f"- Business type: {memory.business_type}\n"
        f"- Services: {_format_services(memory.services)}\n"
        f"- FAQs: {_format_faqs(memory.faqs)}\n"
        f"- Receptionist tone: {memory.receptionist_tone}\n"
        f"- Receptionist personality: {memory.receptionist_personality}\n"
        f"- Refusal behavior: {memory.refusal_behavior}"
    )


def _format_services(services: tuple[str, ...]) -> str:
    if not services:
        return "No services configured. Do not claim service availability."
    return "; ".join(services)


def _format_faqs(faqs: tuple[BusinessFAQ, ...]) -> str:
    if not faqs:
        return "No FAQs configured. Do not invent FAQ answers."
    return " ".join(f"Q: {faq.question} A: {faq.answer}" for faq in faqs)
