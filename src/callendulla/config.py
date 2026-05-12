# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Runtime configuration loaded from environment variables.

A single :class:`Settings` instance is the source of truth. Construct via
:func:`get_settings` (cached) — direct instantiation re-reads env each
time, useful in tests.

Validators reject misconfigurations that would silently degrade security:
- ``SECRET_KEY`` shorter than 32 chars
- ``DIARY_ENCRYPTION_KEY`` that is not a valid Fernet key
- ``LLM_PROVIDER`` that is not one of the supported four
- ``WEBHOOK_SECRET`` shorter than 32 chars when ``BOT_MODE=webhook``
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Annotated

from pydantic import (
    AnyHttpUrl,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class LLMProvider(StrEnum):
    GEMINI = "gemini"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    OLLAMA = "ollama"


class BotMode(StrEnum):
    POLLING = "polling"
    WEBHOOK = "webhook"


class TTSEngine(StrEnum):
    PIPER = "piper"
    EDGE = "edge"
    COSYVOICE = "cosyvoice"


class RegistrationMode(StrEnum):
    OPEN = "open"
    WHITELIST = "whitelist"
    INVITE = "invite"


_DEFAULT_MODELS: dict[LLMProvider, str] = {
    LLMProvider.GEMINI: "gemini-2.5-flash",
    LLMProvider.OPENAI: "gpt-4o-mini",
    LLMProvider.ANTHROPIC: "claude-haiku-4-5-20251001",
    LLMProvider.OLLAMA: "gemma3:12b",
}


def _split_csv(raw: str | list[str] | None) -> list[str]:
    """``"a, b,c"`` → ``["a", "b", "c"]``. Empty / None → ``[]``."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [item.strip() for item in raw if item and item.strip()]
    return [item.strip() for item in raw.split(",") if item.strip()]


class Settings(BaseSettings):
    """All runtime configuration.

    Read once at process start, then shared via :func:`get_settings`. Do
    not mutate after startup — APScheduler/aiogram capture values by
    closure.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ─── Telegram ──────────────────────────────────────────────
    telegram_bot_token: SecretStr = Field(
        ...,
        description="From @BotFather. Format: <bot-id>:<base64-url-token>.",
    )
    owner_tg_id: int = Field(
        ...,
        description="Telegram user_id who gets 'owner' role on first /start.",
    )
    bot_mode: BotMode = BotMode.POLLING
    webhook_host: AnyHttpUrl | None = None
    webhook_path: str | None = Field(
        default=None,
        description="URL path Telegram POSTs to. NOT a secret — appears in nginx logs.",
    )
    webhook_secret: SecretStr | None = Field(
        default=None,
        description="Sent by Telegram in X-Telegram-Bot-Api-Secret-Token header.",
    )

    # ─── LLM ───────────────────────────────────────────────────
    llm_provider: LLMProvider = LLMProvider.GEMINI
    llm_api_key: SecretStr | None = None
    llm_model: str = ""  # resolved against _DEFAULT_MODELS if blank
    ollama_base_url: AnyHttpUrl | None = None
    llm_rate_per_user_hourly: int = Field(default=5, ge=0, le=10_000)

    # ─── Security ──────────────────────────────────────────────
    secret_key: SecretStr = Field(
        ...,
        description="Web session signing key. Generate: openssl rand -hex 32.",
    )
    diary_encryption_key: SecretStr = Field(
        ...,
        description="Fernet key for voice-diary at-rest encryption.",
    )

    # ─── Database ──────────────────────────────────────────────
    db_dsn: str = Field(
        default="postgresql+asyncpg://callendulla:change_me@postgres:5432/callendulla"
    )
    db_dsn_sync: str = Field(
        default="postgresql+psycopg2://callendulla:change_me@postgres:5432/callendulla",
        description="Sync DSN for Alembic — async drivers don't work in migrations.",
    )
    postgres_password: SecretStr | None = None  # compose-only sugar

    # ─── Redis ─────────────────────────────────────────────────
    redis_url: str = "redis://redis:6379/0"

    # ─── Web console ───────────────────────────────────────────
    web_base_url: AnyHttpUrl | None = None
    web_session_days: int = Field(default=7, ge=1, le=365)

    # ─── HTTP hardening ────────────────────────────────────────
    # ``NoDecode`` disables pydantic-settings' default JSON parse for list
    # fields — without it, env values like "a, b, c" raise SettingsError
    # before our @field_validator(mode="before") sees them.
    allowed_hosts: Annotated[list[str], NoDecode, Field(default_factory=lambda: ["*"])]
    cors_origins: Annotated[list[str], NoDecode, Field(default_factory=list)]

    # ─── AGPL §13 ──────────────────────────────────────────────
    agpl_source_url: AnyHttpUrl = Field(
        default=AnyHttpUrl("https://github.com/jetmil/callendulla"),
        description="Returned in X-Source-URL header and /source endpoint.",
    )

    # ─── TTS ───────────────────────────────────────────────────
    tts_engine: TTSEngine = TTSEngine.EDGE
    cosyvoice_base_url: AnyHttpUrl | None = None
    voice_bank_path: str = ""

    # ─── Time & quiet hours ────────────────────────────────────
    default_timezone: str = "Europe/Moscow"
    quiet_from_hour: int = Field(default=22, ge=0, le=23)
    quiet_to_hour: int = Field(default=9, ge=0, le=23)

    # ─── Logging ───────────────────────────────────────────────
    log_level: str = "INFO"
    sentry_dsn: SecretStr | None = None

    # ─── Registration ──────────────────────────────────────────
    registration_mode: RegistrationMode = RegistrationMode.INVITE
    whitelist_tg_usernames: Annotated[list[str], NoDecode, Field(default_factory=list)]
    max_events_per_user: int = Field(default=500, ge=1)
    registration_rate_per_ip_hourly: int = Field(default=10, ge=0)
    ical_rate_limit_per_ip_hourly: int = Field(
        default=60,
        ge=0,
        description=(
            "Sliding-window per-IP limit on /ical/{token} (anti-scraping). "
            "Calendar clients refetch every 5-60 min, 60 hits/h tolerates "
            "even aggressive subscribers. 0 disables the limit."
        ),
    )

    # ─── Validators ────────────────────────────────────────────
    @field_validator("allowed_hosts", "cors_origins", "whitelist_tg_usernames", mode="before")
    @classmethod
    def _parse_csv(cls, v: str | list[str] | None) -> list[str]:
        return _split_csv(v)

    @field_validator("secret_key", mode="after")
    @classmethod
    def _validate_secret_key(cls, v: SecretStr) -> SecretStr:
        if len(v.get_secret_value()) < 32:
            msg = "SECRET_KEY must be at least 32 characters (use openssl rand -hex 32)"
            raise ValueError(msg)
        return v

    @field_validator("diary_encryption_key", mode="after")
    @classmethod
    def _validate_fernet_key(cls, v: SecretStr) -> SecretStr:
        # Lazy import — keeps cryptography out of the dependency graph
        # for callers that only want non-crypto settings (e.g. tests).
        from cryptography.fernet import Fernet, InvalidToken  # noqa: PLC0415

        try:
            Fernet(v.get_secret_value().encode())
        except (ValueError, InvalidToken) as exc:
            msg = (
                "DIARY_ENCRYPTION_KEY is not a valid Fernet key. Generate one: "
                "python -c 'from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())'\""
            )
            raise ValueError(msg) from exc
        return v

    @field_validator("log_level", mode="after")
    @classmethod
    def _validate_log_level(cls, v: str) -> str:
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in valid:
            msg = f"LOG_LEVEL must be one of {sorted(valid)}, got {v!r}"
            raise ValueError(msg)
        return upper

    @model_validator(mode="after")
    def _check_webhook_secret_present(self) -> Settings:
        if self.bot_mode is BotMode.WEBHOOK:
            if self.webhook_host is None:
                msg = "BOT_MODE=webhook requires WEBHOOK_HOST"
                raise ValueError(msg)
            if self.webhook_path is None or not self.webhook_path.startswith("/"):
                msg = "BOT_MODE=webhook requires WEBHOOK_PATH starting with '/'"
                raise ValueError(msg)
            if self.webhook_secret is None or len(self.webhook_secret.get_secret_value()) < 32:
                msg = (
                    "BOT_MODE=webhook requires WEBHOOK_SECRET of at least 32 chars "
                    "(used for X-Telegram-Bot-Api-Secret-Token header check)"
                )
                raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _check_llm_api_key_when_needed(self) -> Settings:
        if self.llm_provider is not LLMProvider.OLLAMA and self.llm_api_key is None:
            msg = f"LLM_API_KEY is required when LLM_PROVIDER={self.llm_provider.value}"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _check_quiet_hours_not_equal(self) -> Settings:
        if self.quiet_from_hour == self.quiet_to_hour:
            msg = (
                f"QUIET_FROM_HOUR ({self.quiet_from_hour}) and QUIET_TO_HOUR "
                f"({self.quiet_to_hour}) must differ — equal values would "
                "silence the bot 24 hours a day"
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _resolve_default_model(self) -> Settings:
        if not self.llm_model:
            self.llm_model = _DEFAULT_MODELS[self.llm_provider]
        return self

    # ─── Helpers ───────────────────────────────────────────────
    @property
    def is_dev(self) -> bool:
        """Heuristic: ``ALLOWED_HOSTS=*`` indicates non-hardened dev setup."""
        return self.allowed_hosts == ["*"]

    def webhook_url(self) -> str | None:
        """Full URL Telegram should POST to, or ``None`` in polling mode."""
        if self.bot_mode is not BotMode.WEBHOOK:
            return None
        # Validators guarantee both host & path are set here.
        return f"{str(self.webhook_host).rstrip('/')}{self.webhook_path}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached singleton accessor.

    The first call reads env and ``.env`` and runs all validators. Later
    callers get the same object. Reset via ``get_settings.cache_clear()``
    in tests that need a different env snapshot.
    """
    return Settings()
