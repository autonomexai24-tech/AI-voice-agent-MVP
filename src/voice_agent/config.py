from __future__ import annotations

import os
import sys
from dataclasses import dataclass
import json
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

_config_logger = None


def _get_config_logger():
    global _config_logger
    if _config_logger is None:
        import logging
        _config_logger = logging.getLogger(__name__)
    return _config_logger


class ConfigError(ValueError):
    """Raised when required runtime configuration is missing or invalid."""


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_project_dotenv(dotenv_path: os.PathLike[str] | str | None = None) -> bool:
    """Load the project .env file before reading process environment."""
    raw_path = Path(dotenv_path) if dotenv_path is not None else PROJECT_ROOT / ".env"
    path = raw_path.expanduser().resolve()

    print(f"[dotenv] current working directory: {Path.cwd()}", file=sys.stderr)
    print(f"[dotenv] config module file: {Path(__file__).resolve()}", file=sys.stderr)
    print(f"[dotenv] PROJECT_ROOT: {PROJECT_ROOT}", file=sys.stderr)
    print(f"[dotenv] PROJECT_ROOT exists: {PROJECT_ROOT.exists()}", file=sys.stderr)
    print(f"[dotenv] resolved .env path: {path}", file=sys.stderr)
    print(f"[dotenv] resolved .env path is absolute: {path.is_absolute()}", file=sys.stderr)
    print(f"[dotenv] .env file exists: {path.exists()}", file=sys.stderr)

    parsed_values = dotenv_values(path) if path.exists() else {}
    parsed_livekit_api_key = parsed_values.get("LIVEKIT_API_KEY")
    before_livekit_api_key = os.environ.get("LIVEKIT_API_KEY")
    print(
        "[dotenv] LIVEKIT_API_KEY parsed from .env before load_dotenv(): "
        f"{bool(parsed_livekit_api_key and parsed_livekit_api_key.strip())}",
        file=sys.stderr,
    )
    print(
        "[dotenv] LIVEKIT_API_KEY exists in process env before load_dotenv(): "
        f"{before_livekit_api_key is not None}",
        file=sys.stderr,
    )
    print(
        "[dotenv] LIVEKIT_API_KEY non-empty in process env before load_dotenv(): "
        f"{bool(before_livekit_api_key and before_livekit_api_key.strip())}",
        file=sys.stderr,
    )

    loaded = load_dotenv(dotenv_path=path, override=False)
    livekit_api_key_loaded = bool(os.getenv("LIVEKIT_API_KEY", "").strip())
    print(f"[dotenv] load_dotenv returned: {loaded}", file=sys.stderr)
    print(
        f"[dotenv] LIVEKIT_API_KEY loaded after load_dotenv(): {livekit_api_key_loaded}",
        file=sys.stderr,
    )
    return loaded


@dataclass(frozen=True)
class LiveKitConfig:
    url: str
    api_key: str
    api_secret: str
    room_name: str
    identity: str
    audio_queue_ms: int
    wait_for_participant_timeout_seconds: float


@dataclass(frozen=True)
class SarvamConfig:
    api_key: str
    tts_url: str
    model: str
    speaker: str
    language_code: str
    sample_rate: int
    tts_timeout_seconds: float


@dataclass(frozen=True)
class SarvamSTTConfig:
    api_key: str
    url: str
    model: str
    mode: str
    language_code: str
    sample_rate: int
    input_audio_codec: str
    audio_encoding: str
    high_vad_sensitivity: bool
    queue_max_chunks: int


@dataclass(frozen=True)
class OpenAIConfig:
    api_key: str
    model: str
    max_output_tokens: int
    timeout_seconds: float
    queue_max_items: int


@dataclass(frozen=True)
class AudioConfig:
    output_sample_rate: int
    frame_ms: int


@dataclass(frozen=True)
class BusinessFAQ:
    question: str
    answer: str


@dataclass(frozen=True)
class BusinessConfig:
    name: str
    business_type: str
    services: tuple[str, ...]
    faqs: tuple[BusinessFAQ, ...]
    receptionist_tone: str
    refusal_behavior: str
    receptionist_personality: str
    context_path: str | None


@dataclass(frozen=True)
class CalComConfig:
    api_key: str | None
    base_url: str
    slots_api_version: str
    bookings_api_version: str
    event_type_id: int | None
    event_type_slug: str | None
    username: str | None
    team_slug: str | None
    organization_slug: str | None
    time_zone: str
    duration_minutes: int
    timeout_seconds: float
    retry_attempts: int
    default_attendee_email: str | None

    @property
    def is_configured(self) -> bool:
        has_event_type = self.event_type_id is not None or (
            self.event_type_slug is not None
            and (self.username is not None or self.team_slug is not None)
        )
        return self.api_key is not None and has_event_type


@dataclass(frozen=True)
class Fast2SMSConfig:
    api_key: str | None
    base_url: str
    route: str
    language: str
    timeout_seconds: float
    retry_attempts: int
    queue_max_items: int
    drain_timeout_seconds: float

    @property
    def is_configured(self) -> bool:
        return self.api_key is not None


@dataclass(frozen=True)
class DatabaseConfig:
    url: str | None
    enabled: bool
    pool_size: int
    max_overflow: int
    pool_timeout_seconds: float
    retry_attempts: int
    retry_backoff_seconds: float
    queue_max_items: int
    drain_timeout_seconds: float

    @property
    def is_configured(self) -> bool:
        return self.enabled and self.url is not None


@dataclass(frozen=True)
class AgentConfig:
    livekit: LiveKitConfig
    sarvam: SarvamConfig
    sarvam_stt: SarvamSTTConfig
    openai: OpenAIConfig
    audio: AudioConfig
    business: BusinessConfig
    calcom: CalComConfig
    fast2sms: Fast2SMSConfig
    database: DatabaseConfig
    greeting_text: str
    disconnect_after_greeting: bool
    log_level: str


def load_config(
    room_name: str | None = None,
    *,
    require_room_name: bool = True,
) -> AgentConfig:
    errors: list[str] = []

    livekit_url = _required("LIVEKIT_URL", errors)
    livekit_api_key = _required("LIVEKIT_API_KEY", errors)
    livekit_api_secret = _required("LIVEKIT_API_SECRET", errors)
    if room_name:
        livekit_room_name = room_name
    elif require_room_name:
        livekit_room_name = _required("LIVEKIT_ROOM_NAME", errors)
    else:
        livekit_room_name = _get("LIVEKIT_ROOM_NAME", "")
    sarvam_api_key = _required("SARVAM_API_KEY", errors)
    openai_api_key = _required("OPENAI_API_KEY", errors)

    greeting_text = _get("GREETING_TEXT", "Hello, this is your AI assistant. How can I help you today?")
    sarvam_sample_rate = _int("SARVAM_SAMPLE_RATE", 16000, errors, minimum=8000)
    sarvam_tts_timeout_seconds = _float("SARVAM_TTS_TIMEOUT_SECONDS", 20.0, errors, minimum=1.0)
    sarvam_stt_sample_rate = _int("SARVAM_STT_SAMPLE_RATE", 16000, errors, minimum=8000)
    sarvam_stt_queue_max_chunks = _int("SARVAM_STT_QUEUE_MAX_CHUNKS", 250, errors, minimum=1)
    openai_max_output_tokens = _int("OPENAI_MAX_OUTPUT_TOKENS", 80, errors, minimum=16)
    openai_queue_max_items = _int("OPENAI_QUEUE_MAX_ITEMS", 50, errors, minimum=1)
    openai_timeout_seconds = _float("OPENAI_TIMEOUT_SECONDS", 12.0, errors, minimum=1.0)
    output_sample_rate = _int("OUTPUT_SAMPLE_RATE", 48000, errors, minimum=8000)
    frame_ms = _int("AUDIO_FRAME_MS", 20, errors, minimum=10)
    audio_queue_ms = _int("LIVEKIT_AUDIO_QUEUE_MS", 250, errors, minimum=20)
    business_services = _list("BUSINESS_SERVICES")
    business_faqs = _faqs(errors)

    _log = _get_config_logger()
    _business_name_raw = os.getenv("BUSINESS_NAME")
    _business_services_raw = os.getenv("BUSINESS_SERVICES")
    _business_faqs_json_raw = os.getenv("BUSINESS_FAQS_JSON")
    _business_faqs_raw = os.getenv("BUSINESS_FAQS")
    print(
        f"[config] BUSINESS_NAME raw={_business_name_raw!r}, "
        f"BUSINESS_SERVICES raw_len={len(_business_services_raw) if _business_services_raw else 0}, "
        f"parsed_count={len(business_services)}, "
        f"parsed_values={business_services!r}, "
        f"BUSINESS_FAQS_JSON raw_len={len(_business_faqs_json_raw) if _business_faqs_json_raw else 0}, "
        f"BUSINESS_FAQS raw_len={len(_business_faqs_raw) if _business_faqs_raw else 0}, "
        f"faqs_parsed_count={len(business_faqs)}",
        file=sys.stderr,
    )
    calcom_duration_minutes = _int("CALCOM_DURATION_MINUTES", 30, errors, minimum=5)
    calcom_timeout_seconds = _float("CALCOM_TIMEOUT_SECONDS", 8.0, errors, minimum=1.0)
    calcom_retry_attempts = _int("CALCOM_RETRY_ATTEMPTS", 1, errors, minimum=0)
    fast2sms_timeout_seconds = _float(
        "FAST2SMS_TIMEOUT_SECONDS",
        5.0,
        errors,
        minimum=1.0,
    )
    fast2sms_retry_attempts = _int("FAST2SMS_RETRY_ATTEMPTS", 1, errors, minimum=0)
    fast2sms_queue_max_items = _int("FAST2SMS_QUEUE_MAX_ITEMS", 100, errors, minimum=1)
    fast2sms_drain_timeout_seconds = _float(
        "FAST2SMS_DRAIN_TIMEOUT_SECONDS",
        3.0,
        errors,
        minimum=0.0,
    )
    database_url = _optional("DATABASE_URL")
    database_enabled = _bool("PERSISTENCE_ENABLED", database_url is not None)
    database_pool_size = _int("DATABASE_POOL_SIZE", 5, errors, minimum=1)
    database_max_overflow = _int("DATABASE_MAX_OVERFLOW", 10, errors, minimum=0)
    database_pool_timeout_seconds = _float(
        "DATABASE_POOL_TIMEOUT_SECONDS",
        5.0,
        errors,
        minimum=0.1,
    )
    database_retry_attempts = _int("DATABASE_RETRY_ATTEMPTS", 2, errors, minimum=0)
    database_retry_backoff_seconds = _float(
        "DATABASE_RETRY_BACKOFF_SECONDS",
        0.05,
        errors,
        minimum=0.0,
    )
    database_queue_max_items = _int("DATABASE_QUEUE_MAX_ITEMS", 500, errors, minimum=1)
    database_drain_timeout_seconds = _float(
        "DATABASE_DRAIN_TIMEOUT_SECONDS",
        2.0,
        errors,
        minimum=0.0,
    )
    participant_timeout = _float(
        "LIVEKIT_WAIT_FOR_PARTICIPANT_TIMEOUT_SECONDS",
        30.0,
        errors,
        minimum=0.1,
    )

    if frame_ms and output_sample_rate and (output_sample_rate * frame_ms) % 1000 != 0:
        errors.append("AUDIO_FRAME_MS must divide evenly into OUTPUT_SAMPLE_RATE sample frames")
    if frame_ms and sarvam_stt_sample_rate and (sarvam_stt_sample_rate * frame_ms) % 1000 != 0:
        errors.append("AUDIO_FRAME_MS must divide evenly into SARVAM_STT_SAMPLE_RATE sample frames")
    if sarvam_stt_sample_rate not in {8000, 16000}:
        errors.append("SARVAM_STT_SAMPLE_RATE must be 8000 or 16000")
    if database_enabled and database_url is None:
        errors.append("DATABASE_URL is required when PERSISTENCE_ENABLED is true")

    if errors:
        raise ConfigError("; ".join(errors))

    return AgentConfig(
        livekit=LiveKitConfig(
            url=livekit_url,
            api_key=livekit_api_key,
            api_secret=livekit_api_secret,
            room_name=livekit_room_name,
            identity=_get("LIVEKIT_AGENT_IDENTITY", "phase-1d-response-agent"),
            audio_queue_ms=audio_queue_ms,
            wait_for_participant_timeout_seconds=participant_timeout,
        ),
        sarvam=SarvamConfig(
            api_key=sarvam_api_key,
            tts_url=_get("SARVAM_TTS_URL", "https://api.sarvam.ai/text-to-speech"),
            model=_get("SARVAM_MODEL", "bulbul:v3"),
            speaker=_get("SARVAM_SPEAKER", "kavya"),
            language_code=_get("SARVAM_LANGUAGE_CODE", "en-IN"),
            sample_rate=sarvam_sample_rate,
            tts_timeout_seconds=sarvam_tts_timeout_seconds,
        ),
        sarvam_stt=SarvamSTTConfig(
            api_key=sarvam_api_key,
            url=_get("SARVAM_STT_URL", "wss://api.sarvam.ai/speech-to-text/ws"),
            model=_get("SARVAM_STT_MODEL", "saaras:v3"),
            mode=_get("SARVAM_STT_MODE", "transcribe"),
            language_code=_get("SARVAM_STT_LANGUAGE_CODE", "en-IN"),
            sample_rate=sarvam_stt_sample_rate,
            input_audio_codec=_get("SARVAM_STT_INPUT_AUDIO_CODEC", "pcm_s16le"),
            audio_encoding=_get("SARVAM_STT_AUDIO_ENCODING", "audio/wav"),
            high_vad_sensitivity=_bool("SARVAM_STT_HIGH_VAD_SENSITIVITY", True),
            queue_max_chunks=sarvam_stt_queue_max_chunks,
        ),
        openai=OpenAIConfig(
            api_key=openai_api_key,
            model=_get("OPENAI_MODEL", "gpt-4o-mini"),
            max_output_tokens=openai_max_output_tokens,
            timeout_seconds=openai_timeout_seconds,
            queue_max_items=openai_queue_max_items,
        ),
        audio=AudioConfig(
            output_sample_rate=output_sample_rate,
            frame_ms=frame_ms,
        ),
        business=BusinessConfig(
            name=_get("BUSINESS_NAME", "our clinic"),
            business_type=_get("BUSINESS_TYPE", "clinic"),
            services=business_services,
            faqs=business_faqs,
            receptionist_tone=_get(
                "RECEPTIONIST_TONE",
                "warm, concise, respectful, and phone-friendly",
            ),
            refusal_behavior=_get(
                "REFUSAL_BEHAVIOR",
                "Sorry sir, I can help only with {business_type}-related questions.",
            ),
            receptionist_personality=_get(
                "RECEPTIONIST_PERSONALITY",
                "calm, attentive, practical, and helpful",
            ),
            context_path=_optional("BUSINESS_CONTEXT_PATH"),
        ),
        calcom=CalComConfig(
            api_key=_optional("CALCOM_API_KEY"),
            base_url=_get("CALCOM_BASE_URL", "https://api.cal.com/v2"),
            slots_api_version=_get("CALCOM_SLOTS_API_VERSION", "2024-09-04"),
            bookings_api_version=_get("CALCOM_BOOKINGS_API_VERSION", "2026-02-25"),
            event_type_id=_optional_int("CALCOM_EVENT_TYPE_ID", errors),
            event_type_slug=_optional("CALCOM_EVENT_TYPE_SLUG"),
            username=_optional("CALCOM_USERNAME"),
            team_slug=_optional("CALCOM_TEAM_SLUG"),
            organization_slug=_optional("CALCOM_ORGANIZATION_SLUG"),
            time_zone=_get("CALCOM_TIME_ZONE", "Asia/Kolkata"),
            duration_minutes=calcom_duration_minutes,
            timeout_seconds=calcom_timeout_seconds,
            retry_attempts=calcom_retry_attempts,
            default_attendee_email=_optional("CALCOM_DEFAULT_ATTENDEE_EMAIL"),
        ),
        fast2sms=Fast2SMSConfig(
            api_key=_optional("FAST2SMS_API_KEY"),
            base_url=_get("FAST2SMS_BASE_URL", "https://www.fast2sms.com/dev/bulkV2"),
            route=_get("FAST2SMS_ROUTE", "q"),
            language=_get("FAST2SMS_LANGUAGE", "english"),
            timeout_seconds=fast2sms_timeout_seconds,
            retry_attempts=fast2sms_retry_attempts,
            queue_max_items=fast2sms_queue_max_items,
            drain_timeout_seconds=fast2sms_drain_timeout_seconds,
        ),
        database=DatabaseConfig(
            url=database_url,
            enabled=database_enabled,
            pool_size=database_pool_size,
            max_overflow=database_max_overflow,
            pool_timeout_seconds=database_pool_timeout_seconds,
            retry_attempts=database_retry_attempts,
            retry_backoff_seconds=database_retry_backoff_seconds,
            queue_max_items=database_queue_max_items,
            drain_timeout_seconds=database_drain_timeout_seconds,
        ),
        greeting_text=greeting_text,
        disconnect_after_greeting=_bool("DISCONNECT_AFTER_GREETING", True),
        log_level=_get("LOG_LEVEL", "INFO"),
    )


def _get(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip()


def _optional(name: str) -> str | None:
    value = os.getenv(name)
    if value is None or not value.strip():
        return None
    return value.strip()


def _required(name: str, errors: list[str]) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        errors.append(f"{name} is required")
        return ""
    return value.strip()


def _int(name: str, default: int, errors: list[str], minimum: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError:
        errors.append(f"{name} must be an integer")
        return default
    if value < minimum:
        errors.append(f"{name} must be >= {minimum}")
    return value


def _optional_int(name: str, errors: list[str]) -> int | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    try:
        value = int(raw)
    except ValueError:
        errors.append(f"{name} must be an integer")
        return None
    if value <= 0:
        errors.append(f"{name} must be > 0")
    return value


def _float(name: str, default: float, errors: list[str], minimum: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        errors.append(f"{name} must be a number")
        return default
    if value < minimum:
        errors.append(f"{name} must be >= {minimum}")
    return value


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _list(name: str) -> tuple[str, ...]:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return ()

    normalized = raw.replace("\r\n", "\n").replace("\r", "\n")
    for separator in ("\n", ";", ","):
        normalized = normalized.replace(separator, "|")
    values = tuple(_dedupe_preserving_order(part.strip() for part in normalized.split("|")))
    return values


def _faqs(errors: list[str]) -> tuple[BusinessFAQ, ...]:
    raw_json = os.getenv("BUSINESS_FAQS_JSON")
    if raw_json is not None and raw_json.strip():
        try:
            payload = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            errors.append(f"BUSINESS_FAQS_JSON must be valid JSON: {exc.msg}")
            return ()

        if not isinstance(payload, list):
            errors.append("BUSINESS_FAQS_JSON must be a list of question/answer objects")
            return ()

        faqs: list[BusinessFAQ] = []
        for index, item in enumerate(payload):
            if not isinstance(item, dict):
                errors.append(f"BUSINESS_FAQS_JSON item {index} must be an object")
                continue
            question = str(item.get("question", "")).strip()
            answer = str(item.get("answer", "")).strip()
            if not question or not answer:
                errors.append(
                    f"BUSINESS_FAQS_JSON item {index} must include question and answer"
                )
                continue
            faqs.append(BusinessFAQ(question=question, answer=answer))
        return tuple(faqs)

    raw_pairs = os.getenv("BUSINESS_FAQS")
    if raw_pairs is None or not raw_pairs.strip():
        return ()

    faqs = []
    for index, pair in enumerate(raw_pairs.split(";")):
        if not pair.strip():
            continue
        if "=" not in pair:
            errors.append(f"BUSINESS_FAQS item {index} must use question=answer format")
            continue
        question, answer = pair.split("=", 1)
        question = question.strip()
        answer = answer.strip()
        if not question or not answer:
            errors.append(f"BUSINESS_FAQS item {index} must include question and answer")
            continue
        faqs.append(BusinessFAQ(question=question, answer=answer))
    return tuple(faqs)


def _dedupe_preserving_order(values: object) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        text = str(value).strip()
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        deduped.append(text)
    return deduped
