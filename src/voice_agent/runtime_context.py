"""Runtime Context Loader — PostgreSQL-first business context for every call.

This module replaces the .env-only business context path with a PostgreSQL-first
architecture.  On every incoming call the worker loads the latest business
settings from the database, converts them to the canonical ``BusinessConfig``,
and wraps everything in a ``RuntimeContextSnapshot`` for traceability.

If the database is unavailable or the settings row does not exist the loader
falls back to the ``.env``-sourced ``BusinessConfig`` that ``load_config()``
already provides.  This guarantees zero-downtime — a DB outage degrades
gracefully to the previous static behavior instead of crashing.

Flow:
    Frontend → PostgreSQL → RuntimeContextLoader → BusinessConfig → Worker
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from database.models.business_settings import BusinessSettingsModel
from database.repositories.business_settings import (
    BusinessSettingsRepository,
    DEFAULT_SETTINGS_ID,
)
from database.session import (
    DatabaseSettings,
    create_engine,
    create_session_factory,
    session_scope,
)
from voice_agent.config import BusinessConfig, BusinessFAQ
from voice_agent.logging_config import get_logger, log_error, log_event

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# RuntimeContextSnapshot — immutable per-call record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RuntimeContextSnapshot:
    """Immutable context snapshot captured at call start.

    Every call gets its own snapshot so the business settings active at the
    moment the call was answered are recorded.  This prevents mid-call
    mutations and provides an auditable record of which context was used.
    """

    business: BusinessConfig
    context_source: str
    settings_id: str | None
    loaded_at: datetime
    latency_ms: float
    default_language: str
    greeting_prompt: str | None


# ---------------------------------------------------------------------------
# BusinessConfig ↔ DB model conversion
# ---------------------------------------------------------------------------

_DEFAULT_TONE = "warm, concise, respectful, and phone-friendly"
_DEFAULT_PERSONALITY = "calm, attentive, practical, and helpful"
_DEFAULT_REFUSAL = "Sorry sir, I can help only with {business_type}-related questions."


def business_config_from_db(model: BusinessSettingsModel) -> BusinessConfig:
    """Convert a ``BusinessSettingsModel`` row into a ``BusinessConfig``."""
    faqs = _parse_faqs(model.faqs)
    return BusinessConfig(
        name=model.business_name or "our clinic",
        business_type=model.business_type or "clinic",
        services=tuple(model.services) if model.services else (),
        faqs=faqs,
        receptionist_tone=model.receptionist_tone or _DEFAULT_TONE,
        refusal_behavior=model.refusal_policy or _DEFAULT_REFUSAL,
        receptionist_personality=model.receptionist_personality or _DEFAULT_PERSONALITY,
        context_path=None,
    )


def _parse_faqs(raw: list[dict[str, Any]] | None) -> tuple[BusinessFAQ, ...]:
    """Safely parse the JSON ``faqs`` column into typed FAQ tuples."""
    if not raw:
        return ()
    parsed: list[BusinessFAQ] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question", "")).strip()
        answer = str(item.get("answer", "")).strip()
        if question and answer:
            parsed.append(BusinessFAQ(question=question, answer=answer))
    return tuple(parsed)


# ---------------------------------------------------------------------------
# RuntimeContextLoader
# ---------------------------------------------------------------------------


class RuntimeContextLoader:
    """Loads business context from PostgreSQL with .env fallback.

    Designed to be called once per incoming call (inside the worker
    ``entrypoint``).  The loader opens a short-lived DB session, reads
    the current ``business_settings`` row, converts it to ``BusinessConfig``,
    and returns a ``RuntimeContextSnapshot``.

    Thread / task safety: each call to ``load()`` creates its own session
    so concurrent calls are isolated.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        settings_id: str = DEFAULT_SETTINGS_ID,
    ) -> None:
        self._session_factory = session_factory
        self._settings_id = settings_id

    async def load(
        self,
        *,
        env_fallback: BusinessConfig,
        env_default_language: str = "english",
        env_greeting_prompt: str | None = None,
    ) -> RuntimeContextSnapshot:
        """Load business context from PostgreSQL.

        Parameters
        ----------
        env_fallback:
            The ``BusinessConfig`` produced by ``load_config()`` from ``.env``.
            Used when the database row is missing or the query fails.
        env_default_language:
            Fallback default language from ``.env``.  Only used when DB is
            unavailable.
        env_greeting_prompt:
            Fallback greeting from ``.env``.

        Returns
        -------
        RuntimeContextSnapshot
            An immutable snapshot of the business context for this call.
        """
        started_at = time.perf_counter()

        try:
            async with session_scope(self._session_factory) as session:
                repo = BusinessSettingsRepository(session)
                model = await repo.get(self._settings_id)

            if model is None:
                latency_ms = round((time.perf_counter() - started_at) * 1000, 3)
                log_event(
                    logger,
                    "runtime_context_loaded",
                    context_source="env_fallback",
                    reason="no_db_row",
                    settings_id=self._settings_id,
                    business_name=env_fallback.name,
                    services_count=len(env_fallback.services),
                    faqs_count=len(env_fallback.faqs),
                    latency_ms=latency_ms,
                )
                return RuntimeContextSnapshot(
                    business=env_fallback,
                    context_source="env_fallback",
                    settings_id=None,
                    loaded_at=datetime.now(timezone.utc),
                    latency_ms=latency_ms,
                    default_language=env_default_language,
                    greeting_prompt=env_greeting_prompt,
                )

            business = business_config_from_db(model)
            latency_ms = round((time.perf_counter() - started_at) * 1000, 3)
            log_event(
                logger,
                "runtime_context_loaded",
                context_source="database",
                settings_id=model.settings_id,
                business_name=business.name,
                business_type=business.business_type,
                services_count=len(business.services),
                services=list(business.services),
                faqs_count=len(business.faqs),
                receptionist_tone=business.receptionist_tone,
                receptionist_personality=business.receptionist_personality,
                default_language=model.default_language,
                greeting_prompt=model.greeting_prompt,
                latency_ms=latency_ms,
            )
            return RuntimeContextSnapshot(
                business=business,
                context_source="database",
                settings_id=model.settings_id,
                loaded_at=datetime.now(timezone.utc),
                latency_ms=latency_ms,
                default_language=model.default_language or "english",
                greeting_prompt=model.greeting_prompt,
            )

        except Exception as exc:
            latency_ms = round((time.perf_counter() - started_at) * 1000, 3)
            log_error(
                logger,
                "runtime_context_db_failed",
                settings_id=self._settings_id,
                error_type=type(exc).__name__,
                error=str(exc),
                latency_ms=latency_ms,
            )
            log_event(
                logger,
                "runtime_context_loaded",
                context_source="env_fallback",
                reason="db_error",
                settings_id=self._settings_id,
                business_name=env_fallback.name,
                services_count=len(env_fallback.services),
                faqs_count=len(env_fallback.faqs),
                latency_ms=latency_ms,
            )
            return RuntimeContextSnapshot(
                business=env_fallback,
                context_source="env_fallback",
                settings_id=None,
                loaded_at=datetime.now(timezone.utc),
                latency_ms=latency_ms,
                default_language=env_default_language,
                greeting_prompt=env_greeting_prompt,
            )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_runtime_context_loader(
    settings: DatabaseSettings,
    *,
    settings_id: str = DEFAULT_SETTINGS_ID,
) -> RuntimeContextLoader | None:
    """Create a ``RuntimeContextLoader`` if the database is configured.

    Returns ``None`` when the database is disabled or has no URL — the worker
    should then use ``.env`` business context directly.
    """
    if not settings.is_configured:
        log_event(
            logger,
            "runtime_context_loader_skipped",
            reason="database_not_configured",
        )
        return None

    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    log_event(
        logger,
        "runtime_context_loader_created",
        settings_id=settings_id,
    )
    return RuntimeContextLoader(session_factory, settings_id=settings_id)
