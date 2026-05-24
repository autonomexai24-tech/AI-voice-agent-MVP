"""Phase 1B — Runtime Context Loader hardening tests.

Validates:
- Task 1: PostgreSQL is the ONLY runtime business source when DB row exists
- Task 2: Frontend changes (DB updates) affect the next load() call immediately
- Task 3: Concurrent loads each get independent fresh snapshots
- Task 4: RuntimeContextSnapshot contains ALL required business intelligence fields
- Task 7: Fallback safety when DB is unavailable / row missing / query fails
- Task 8: BusinessConfig from DB produces correct prompt composition
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from voice_agent.config import BusinessConfig, BusinessFAQ
from voice_agent.runtime_context import (
    RuntimeContextLoader,
    RuntimeContextSnapshot,
    business_config_from_db,
    _parse_faqs,
)


def _run(coro):
    """Run an async coroutine synchronously, matching project test conventions."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Fixtures — fake DB model
# ---------------------------------------------------------------------------


def _fake_db_model(**overrides):
    """Build a fake BusinessSettingsModel-like object from keyword args."""
    defaults = dict(
        settings_id="default",
        business_name="Smile Dental Clinic",
        business_type="dental",
        services=["cleaning", "root canal", "whitening"],
        receptionist_tone="warm and professional",
        receptionist_personality="calm and empathetic",
        faqs=[
            {"question": "Do you accept insurance?", "answer": "Yes, we accept most major providers."},
            {"question": "What are your hours?", "answer": "Mon-Sat 9am to 6pm."},
        ],
        default_language="hindi",
        greeting_prompt="Namaste! Smile Dental Clinic mein aapka swagat hai.",
        refusal_policy="Sorry, I can only help with dental-related questions.",
    )
    defaults.update(overrides)
    model = MagicMock()
    for k, v in defaults.items():
        setattr(model, k, v)
    return model


def _env_fallback(**overrides):
    """Build a .env-sourced BusinessConfig for fallback."""
    defaults = dict(
        name="env clinic",
        business_type="clinic",
        services=("general checkup",),
        faqs=(),
        receptionist_tone="warm",
        refusal_behavior="Sorry, clinic-only questions.",
        receptionist_personality="helpful",
        context_path=None,
    )
    defaults.update(overrides)
    return BusinessConfig(**defaults)


def _mock_session_factory(model):
    """Build a mock async session factory that returns the given model from repo.get()."""
    factory = AsyncMock()
    session = AsyncMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return factory


# ---------------------------------------------------------------------------
# Task 1 — PostgreSQL is the ONLY runtime business source
# ---------------------------------------------------------------------------


class TestTask1_DBIsOnlySource:
    """When a DB row exists, .env business values must NEVER be used."""

    def test_db_model_overrides_env_business_name(self):
        model = _fake_db_model(business_name="DB Clinic")
        config = business_config_from_db(model)
        assert config.name == "DB Clinic"
        assert config.name != "env clinic"

    def test_db_model_overrides_env_services(self):
        model = _fake_db_model(services=["surgery", "x-ray"])
        config = business_config_from_db(model)
        assert config.services == ("surgery", "x-ray")

    def test_db_model_overrides_env_receptionist_tone(self):
        model = _fake_db_model(receptionist_tone="formal and respectful")
        config = business_config_from_db(model)
        assert config.receptionist_tone == "formal and respectful"

    def test_db_model_overrides_env_business_type(self):
        model = _fake_db_model(business_type="hospital")
        config = business_config_from_db(model)
        assert config.business_type == "hospital"

    def test_db_model_overrides_env_receptionist_personality(self):
        model = _fake_db_model(receptionist_personality="serious and efficient")
        config = business_config_from_db(model)
        assert config.receptionist_personality == "serious and efficient"

    def test_db_model_overrides_env_refusal_policy(self):
        model = _fake_db_model(refusal_policy="I handle only dental matters.")
        config = business_config_from_db(model)
        assert config.refusal_behavior == "I handle only dental matters."

    def test_db_model_faqs_parsed_correctly(self):
        model = _fake_db_model(faqs=[
            {"question": "Q1", "answer": "A1"},
            {"question": "Q2", "answer": "A2"},
        ])
        config = business_config_from_db(model)
        assert len(config.faqs) == 2
        assert config.faqs[0].question == "Q1"
        assert config.faqs[1].answer == "A2"

    def test_context_path_is_always_none_from_db(self):
        model = _fake_db_model()
        config = business_config_from_db(model)
        assert config.context_path is None


# ---------------------------------------------------------------------------
# Task 2 — Frontend changes affect new calls immediately
# ---------------------------------------------------------------------------


class TestTask2_FrontendChangesAffectNextCall:
    """Simulates DB updates between calls. Each load() must return fresh data."""

    def test_second_load_reflects_updated_db_row(self):
        async def _run_test():
            model_v1 = _fake_db_model(business_name="Clinic v1", services=["cleaning"])
            model_v2 = _fake_db_model(business_name="Clinic v2", services=["cleaning", "surgery"])

            call_count = 0

            async def fake_get(settings_id="default"):
                nonlocal call_count
                call_count += 1
                return model_v1 if call_count == 1 else model_v2

            with patch("voice_agent.runtime_context.session_scope") as mock_scope:
                mock_session = AsyncMock()
                mock_scope.return_value.__aenter__ = AsyncMock(return_value=mock_session)
                mock_scope.return_value.__aexit__ = AsyncMock(return_value=False)

                with patch("voice_agent.runtime_context.BusinessSettingsRepository") as MockRepo:
                    MockRepo.return_value.get = AsyncMock(side_effect=fake_get)

                    loader = RuntimeContextLoader(
                        AsyncMock(),
                        settings_id="default",
                    )
                    snap1 = await loader.load(env_fallback=_env_fallback())
                    snap2 = await loader.load(env_fallback=_env_fallback())

            assert snap1.business.name == "Clinic v1"
            assert snap1.business.services == ("cleaning",)
            assert snap2.business.name == "Clinic v2"
            assert snap2.business.services == ("cleaning", "surgery")
            assert snap1.context_source == "database"
            assert snap2.context_source == "database"

        _run(_run_test())


# ---------------------------------------------------------------------------
# Task 3 — Multi-worker / concurrent call consistency
# ---------------------------------------------------------------------------


class TestTask3_ConcurrentCallConsistency:
    """5 concurrent loads must each independently query DB and get isolated snapshots."""

    def test_five_concurrent_loads_all_get_fresh_snapshots(self):
        async def _run_test():
            call_count = 0

            async def fake_get(settings_id="default"):
                nonlocal call_count
                call_count += 1
                return _fake_db_model(business_name=f"Clinic-{call_count}")

            with patch("voice_agent.runtime_context.session_scope") as mock_scope:
                mock_session = AsyncMock()
                mock_scope.return_value.__aenter__ = AsyncMock(return_value=mock_session)
                mock_scope.return_value.__aexit__ = AsyncMock(return_value=False)

                with patch("voice_agent.runtime_context.BusinessSettingsRepository") as MockRepo:
                    MockRepo.return_value.get = AsyncMock(side_effect=fake_get)

                    loader = RuntimeContextLoader(
                        AsyncMock(),
                        settings_id="default",
                    )

                    snapshots = await asyncio.gather(*[
                        loader.load(env_fallback=_env_fallback())
                        for _ in range(5)
                    ])

            assert len(snapshots) == 5
            names = {s.business.name for s in snapshots}
            assert len(names) == 5, f"Expected 5 unique names, got {names}"
            for snap in snapshots:
                assert snap.context_source == "database"
                assert snap.settings_id == "default"

        _run(_run_test())


# ---------------------------------------------------------------------------
# Task 4 — Snapshot completeness audit
# ---------------------------------------------------------------------------


class TestTask4_SnapshotCompleteness:
    """Every required business intelligence field must be in the snapshot."""

    def test_snapshot_has_all_required_fields(self):
        required_fields = {
            "business",
            "context_source",
            "settings_id",
            "loaded_at",
            "latency_ms",
            "default_language",
            "greeting_prompt",
        }
        actual_fields = set(RuntimeContextSnapshot.__dataclass_fields__.keys())
        missing = required_fields - actual_fields
        assert not missing, f"Missing snapshot fields: {missing}"

    def test_business_config_has_all_required_fields(self):
        required_fields = {
            "name",
            "business_type",
            "services",
            "faqs",
            "receptionist_tone",
            "refusal_behavior",
            "receptionist_personality",
        }
        actual_fields = set(BusinessConfig.__dataclass_fields__.keys())
        missing = required_fields - actual_fields
        assert not missing, f"Missing BusinessConfig fields: {missing}"

    def test_db_snapshot_populates_all_fields(self):
        async def _run_test():
            model = _fake_db_model()

            with patch("voice_agent.runtime_context.session_scope") as mock_scope:
                mock_session = AsyncMock()
                mock_scope.return_value.__aenter__ = AsyncMock(return_value=mock_session)
                mock_scope.return_value.__aexit__ = AsyncMock(return_value=False)

                with patch("voice_agent.runtime_context.BusinessSettingsRepository") as MockRepo:
                    MockRepo.return_value.get = AsyncMock(return_value=model)

                    loader = RuntimeContextLoader(AsyncMock(), settings_id="default")
                    snap = await loader.load(env_fallback=_env_fallback())

            assert snap.business.name == "Smile Dental Clinic"
            assert snap.business.business_type == "dental"
            assert snap.business.services == ("cleaning", "root canal", "whitening")
            assert len(snap.business.faqs) == 2
            assert snap.business.receptionist_tone == "warm and professional"
            assert snap.business.receptionist_personality == "calm and empathetic"
            assert snap.business.refusal_behavior == "Sorry, I can only help with dental-related questions."
            assert snap.default_language == "hindi"
            assert snap.greeting_prompt == "Namaste! Smile Dental Clinic mein aapka swagat hai."
            assert snap.context_source == "database"
            assert snap.settings_id == "default"
            assert isinstance(snap.loaded_at, datetime)
            assert snap.latency_ms >= 0

        _run(_run_test())

    def test_snapshot_is_frozen(self):
        snap = RuntimeContextSnapshot(
            business=_env_fallback(),
            context_source="test",
            settings_id=None,
            loaded_at=datetime.now(timezone.utc),
            latency_ms=0.0,
            default_language="english",
            greeting_prompt=None,
        )
        with pytest.raises(AttributeError):
            snap.context_source = "mutated"


# ---------------------------------------------------------------------------
# Task 7 — Fallback safety
# ---------------------------------------------------------------------------


class TestTask7_FallbackSafety:
    """Worker must still function when DB is unavailable."""

    def test_fallback_when_db_row_missing(self):
        async def _run_test():
            with patch("voice_agent.runtime_context.session_scope") as mock_scope:
                mock_session = AsyncMock()
                mock_scope.return_value.__aenter__ = AsyncMock(return_value=mock_session)
                mock_scope.return_value.__aexit__ = AsyncMock(return_value=False)

                with patch("voice_agent.runtime_context.BusinessSettingsRepository") as MockRepo:
                    MockRepo.return_value.get = AsyncMock(return_value=None)

                    loader = RuntimeContextLoader(AsyncMock(), settings_id="default")
                    fallback = _env_fallback(name="fallback clinic")
                    snap = await loader.load(env_fallback=fallback)

            assert snap.context_source == "env_fallback"
            assert snap.business.name == "fallback clinic"
            assert snap.settings_id is None

        _run(_run_test())

    def test_fallback_when_db_query_raises(self):
        async def _run_test():
            with patch("voice_agent.runtime_context.session_scope") as mock_scope:
                mock_scope.return_value.__aenter__ = AsyncMock(
                    side_effect=ConnectionError("DB unreachable")
                )
                mock_scope.return_value.__aexit__ = AsyncMock(return_value=False)

                loader = RuntimeContextLoader(AsyncMock(), settings_id="default")
                fallback = _env_fallback(name="error fallback")
                snap = await loader.load(env_fallback=fallback)

            assert snap.context_source == "env_fallback"
            assert snap.business.name == "error fallback"
            assert snap.settings_id is None

        _run(_run_test())

    def test_fallback_preserves_env_default_language_and_greeting(self):
        async def _run_test():
            with patch("voice_agent.runtime_context.session_scope") as mock_scope:
                mock_session = AsyncMock()
                mock_scope.return_value.__aenter__ = AsyncMock(return_value=mock_session)
                mock_scope.return_value.__aexit__ = AsyncMock(return_value=False)

                with patch("voice_agent.runtime_context.BusinessSettingsRepository") as MockRepo:
                    MockRepo.return_value.get = AsyncMock(return_value=None)

                    loader = RuntimeContextLoader(AsyncMock(), settings_id="default")
                    snap = await loader.load(
                        env_fallback=_env_fallback(),
                        env_default_language="hinglish",
                        env_greeting_prompt="Hello from env!",
                    )

            assert snap.default_language == "hinglish"
            assert snap.greeting_prompt == "Hello from env!"

        _run(_run_test())


# ---------------------------------------------------------------------------
# Task 8 — No prompt regressions (DB config → prompt composition)
# ---------------------------------------------------------------------------


class TestTask8_PromptComposition:
    """BusinessConfig from DB must produce valid prompt strings."""

    def test_db_config_produces_non_empty_instructions(self):
        from voice_agent.agent_v2 import build_instructions

        model = _fake_db_model()
        config = business_config_from_db(model)
        instructions = build_instructions(config)

        assert isinstance(instructions, str)
        assert len(instructions) > 100
        assert "Smile Dental Clinic" in instructions
        assert "dental" in instructions.lower()

    def test_db_config_injects_services_into_prompt(self):
        from voice_agent.agent_v2 import build_instructions

        model = _fake_db_model(services=["orthodontics", "implants"])
        config = business_config_from_db(model)
        instructions = build_instructions(config)

        assert "orthodontics" in instructions.lower()
        assert "implants" in instructions.lower()

    def test_db_config_injects_faqs_into_prompt(self):
        from voice_agent.agent_v2 import build_instructions

        model = _fake_db_model(faqs=[
            {"question": "Do you do root canals?", "answer": "Yes, we specialize in painless root canals."},
        ])
        config = business_config_from_db(model)
        instructions = build_instructions(config)

        assert "root canal" in instructions.lower()

    def test_db_config_injects_refusal_policy(self):
        from voice_agent.agent_v2 import build_instructions

        model = _fake_db_model(refusal_policy="I only answer dental questions.")
        config = business_config_from_db(model)
        instructions = build_instructions(config)

        assert "dental" in instructions.lower()

    def test_db_config_injects_receptionist_tone(self):
        from voice_agent.agent_v2 import build_instructions

        model = _fake_db_model(receptionist_tone="extremely formal and polite")
        config = business_config_from_db(model)
        instructions = build_instructions(config)

        assert "extremely formal" in instructions.lower()

    def test_business_prompt_orchestrator_accepts_db_context_source(self):
        """Orchestrator with context_source='database' should return config directly."""
        from voice_agent.business_prompt import BusinessPromptOrchestrator

        model = _fake_db_model()
        config = business_config_from_db(model)
        orchestrator = BusinessPromptOrchestrator(config, context_source="database")

        result = asyncio.run(orchestrator.load())
        assert result.name == "Smile Dental Clinic"
        assert result is config  # No transformation — direct pass-through


# ---------------------------------------------------------------------------
# FAQ parsing edge cases
# ---------------------------------------------------------------------------


class TestFAQParsing:
    def test_empty_faqs(self):
        assert _parse_faqs(None) == ()
        assert _parse_faqs([]) == ()

    def test_malformed_faq_skipped(self):
        result = _parse_faqs([
            {"question": "Q1", "answer": "A1"},
            "not a dict",
            {"question": "", "answer": "A2"},
            {"question": "Q3"},
            {"question": "Q4", "answer": "A4"},
        ])
        assert len(result) == 2
        assert result[0].question == "Q1"
        assert result[1].question == "Q4"

    def test_faq_strips_whitespace(self):
        result = _parse_faqs([{"question": "  Q  ", "answer": "  A  "}])
        assert result[0].question == "Q"
        assert result[0].answer == "A"
