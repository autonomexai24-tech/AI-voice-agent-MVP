from __future__ import annotations

import pytest

from voice_agent.config import ConfigError, load_config, load_project_dotenv


def test_load_config_requires_external_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [
        "LIVEKIT_URL",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
        "LIVEKIT_ROOM_NAME",
        "SARVAM_API_KEY",
        "OPENAI_API_KEY",
    ]:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ConfigError) as exc_info:
        load_config()

    message = str(exc_info.value)
    assert "LIVEKIT_URL is required" in message
    assert "SARVAM_API_KEY is required" in message
    assert "OPENAI_API_KEY is required" in message


def test_load_project_dotenv_loads_values_before_config_validation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    for name in [
        "LIVEKIT_URL",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
        "LIVEKIT_ROOM_NAME",
        "SARVAM_API_KEY",
        "OPENAI_API_KEY",
    ]:
        monkeypatch.delenv(name, raising=False)

    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "LIVEKIT_URL=wss://example.livekit.cloud",
                "LIVEKIT_API_KEY=lk-key",
                "LIVEKIT_API_SECRET=lk-secret",
                "LIVEKIT_ROOM_NAME=env-room",
                "SARVAM_API_KEY=sarvam-key",
                "OPENAI_API_KEY=openai-key",
            ]
        ),
        encoding="utf-8",
    )

    assert load_project_dotenv(env_file) is True

    config = load_config()

    assert config.livekit.room_name == "env-room"
    assert config.sarvam.api_key == "sarvam-key"
    assert config.openai.api_key == "openai-key"


def test_room_cli_override_replaces_env_room(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIVEKIT_URL", "wss://example.livekit.cloud")
    monkeypatch.setenv("LIVEKIT_API_KEY", "lk-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "lk-secret")
    monkeypatch.setenv("LIVEKIT_ROOM_NAME", "env-room")
    monkeypatch.setenv("SARVAM_API_KEY", "sarvam-key")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")

    config = load_config(room_name="cli-room")

    assert config.livekit.room_name == "cli-room"
    assert config.openai.model == "gpt-4o-mini"
    assert config.disconnect_after_greeting is True


def test_load_config_parses_business_context(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIVEKIT_URL", "wss://example.livekit.cloud")
    monkeypatch.setenv("LIVEKIT_API_KEY", "lk-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "lk-secret")
    monkeypatch.setenv("LIVEKIT_ROOM_NAME", "env-room")
    monkeypatch.setenv("SARVAM_API_KEY", "sarvam-key")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("BUSINESS_NAME", "Smile Dental Clinic")
    monkeypatch.setenv("BUSINESS_TYPE", "clinic")
    monkeypatch.setenv("BUSINESS_SERVICES", "braces treatment; dental cleaning")
    monkeypatch.setenv(
        "BUSINESS_FAQS_JSON",
        '[{"question":"What are your hours?","answer":"10 AM to 7 PM."}]',
    )
    monkeypatch.setenv("RECEPTIONIST_TONE", "warm")
    monkeypatch.setenv("REFUSAL_BEHAVIOR", "Clinic questions only, please.")
    monkeypatch.setenv("RECEPTIONIST_PERSONALITY", "calm")

    config = load_config()

    assert config.business.name == "Smile Dental Clinic"
    assert config.business.business_type == "clinic"
    assert config.business.services == ("braces treatment", "dental cleaning")
    assert len(config.business.faqs) == 1
    assert config.business.faqs[0].answer == "10 AM to 7 PM."
    assert config.business.receptionist_tone == "warm"
    assert config.business.refusal_behavior == "Clinic questions only, please."
    assert config.business.receptionist_personality == "calm"


def test_load_config_parses_calcom_booking_context(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIVEKIT_URL", "wss://example.livekit.cloud")
    monkeypatch.setenv("LIVEKIT_API_KEY", "lk-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "lk-secret")
    monkeypatch.setenv("LIVEKIT_ROOM_NAME", "env-room")
    monkeypatch.setenv("SARVAM_API_KEY", "sarvam-key")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("CALCOM_API_KEY", "cal-key")
    monkeypatch.setenv("CALCOM_EVENT_TYPE_ID", "123")
    monkeypatch.setenv("CALCOM_TIME_ZONE", "Asia/Kolkata")
    monkeypatch.setenv("CALCOM_DURATION_MINUTES", "45")
    monkeypatch.setenv("CALCOM_TIMEOUT_SECONDS", "4")
    monkeypatch.setenv("CALCOM_RETRY_ATTEMPTS", "2")
    monkeypatch.setenv("CALCOM_DEFAULT_ATTENDEE_EMAIL", "reception@example.com")
    monkeypatch.setenv("FAST2SMS_API_KEY", "sms-key")
    monkeypatch.setenv("FAST2SMS_TIMEOUT_SECONDS", "6")
    monkeypatch.setenv("FAST2SMS_RETRY_ATTEMPTS", "1")
    monkeypatch.setenv("FAST2SMS_QUEUE_MAX_ITEMS", "25")
    monkeypatch.setenv("FAST2SMS_DRAIN_TIMEOUT_SECONDS", "2")

    config = load_config()

    assert config.calcom.api_key == "cal-key"
    assert config.calcom.event_type_id == 123
    assert config.calcom.time_zone == "Asia/Kolkata"
    assert config.calcom.duration_minutes == 45
    assert config.calcom.timeout_seconds == 4
    assert config.calcom.retry_attempts == 2
    assert config.calcom.default_attendee_email == "reception@example.com"
    assert config.calcom.is_configured is True
    assert config.fast2sms.api_key == "sms-key"
    assert config.fast2sms.base_url == "https://www.fast2sms.com/dev/bulkV2"
    assert config.fast2sms.route == "q"
    assert config.fast2sms.language == "english"
    assert config.fast2sms.timeout_seconds == 6
    assert config.fast2sms.retry_attempts == 1
    assert config.fast2sms.queue_max_items == 25
    assert config.fast2sms.drain_timeout_seconds == 2
    assert config.fast2sms.is_configured is True
