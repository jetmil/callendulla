# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Tests for :mod:`callendulla.config`."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from cryptography.fernet import Fernet

from callendulla.config import (
    BotMode,
    LLMProvider,
    RegistrationMode,
    Settings,
    TTSEngine,
    get_settings,
)

_FERNET_KEY = Fernet.generate_key().decode()
_SECRET_32 = "a" * 32


def _minimal_env() -> dict[str, str]:
    """Smallest env that should construct ``Settings()`` cleanly."""
    return {
        "TELEGRAM_BOT_TOKEN": "1234567890:AAFAKEFAKEFAKEFAKEFAKEFAKE00000000",
        "OWNER_TG_ID": "42",
        "LLM_PROVIDER": "gemini",
        "LLM_API_KEY": "AIzaSyFAKEFAKEFAKEFAKEFAKEFAKEFAKE000",
        "SECRET_KEY": _SECRET_32,
        "DIARY_ENCRYPTION_KEY": _FERNET_KEY,
    }


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, str]]:
    """Inject the minimal env. Caller mutates the returned dict to test variants."""
    data = _minimal_env()
    # Clear any inherited values that could shadow our test ones.
    for key in (
        *data,
        "BOT_MODE",
        "WEBHOOK_HOST",
        "WEBHOOK_PATH",
        "WEBHOOK_SECRET",
        "LLM_MODEL",
        "ALLOWED_HOSTS",
        "CORS_ORIGINS",
        "QUIET_FROM_HOUR",
        "QUIET_TO_HOUR",
        "LOG_LEVEL",
        "REGISTRATION_MODE",
        "WHITELIST_TG_USERNAMES",
        "WEB_BASE_URL",
        "AGPL_SOURCE_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    for k, v in data.items():
        monkeypatch.setenv(k, v)
    # Bypass .env file lookup so the project's local .env never bleeds in.
    monkeypatch.chdir("/tmp")
    get_settings.cache_clear()
    yield data
    get_settings.cache_clear()


class TestMinimalEnv:
    def test_constructs(self, env: dict[str, str]) -> None:
        s = Settings()
        assert s.owner_tg_id == 42
        assert s.llm_provider is LLMProvider.GEMINI
        assert s.bot_mode is BotMode.POLLING
        assert s.tts_engine is TTSEngine.EDGE
        assert s.registration_mode is RegistrationMode.INVITE

    def test_secret_key_redacted_in_repr(self, env: dict[str, str]) -> None:
        s = Settings()
        text = repr(s)
        assert _SECRET_32 not in text
        assert _FERNET_KEY not in text

    def test_default_model_resolved(self, env: dict[str, str]) -> None:
        s = Settings()
        assert s.llm_model == "gemini-2.5-flash"

    def test_explicit_model_kept(
        self, env: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LLM_MODEL", "gemini-2.5-pro")
        s = Settings()
        assert s.llm_model == "gemini-2.5-pro"


class TestSecretKeyValidation:
    def test_too_short_rejected(self, env: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SECRET_KEY", "short")
        with pytest.raises(ValueError, match="SECRET_KEY must be at least 32"):
            Settings()


class TestFernetValidation:
    def test_invalid_fernet_rejected(
        self, env: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DIARY_ENCRYPTION_KEY", "not-a-fernet-key")
        with pytest.raises(ValueError, match="not a valid Fernet key"):
            Settings()


class TestWebhookValidation:
    def test_polling_does_not_require_webhook(self, env: dict[str, str]) -> None:
        Settings()  # no raise

    def test_webhook_requires_all_three(
        self, env: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BOT_MODE", "webhook")
        with pytest.raises(ValueError, match="WEBHOOK_HOST"):
            Settings()

    def test_webhook_secret_must_be_long(
        self, env: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BOT_MODE", "webhook")
        monkeypatch.setenv("WEBHOOK_HOST", "https://example.com")
        monkeypatch.setenv("WEBHOOK_PATH", "/tg/abc123")
        monkeypatch.setenv("WEBHOOK_SECRET", "tooshort")
        with pytest.raises(ValueError, match="WEBHOOK_SECRET of at least 32"):
            Settings()

    def test_webhook_path_must_start_with_slash(
        self, env: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BOT_MODE", "webhook")
        monkeypatch.setenv("WEBHOOK_HOST", "https://example.com")
        monkeypatch.setenv("WEBHOOK_PATH", "no-slash")
        monkeypatch.setenv("WEBHOOK_SECRET", "a" * 32)
        with pytest.raises(ValueError, match="WEBHOOK_PATH starting with '/'"):
            Settings()

    def test_webhook_url_assembled(
        self, env: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BOT_MODE", "webhook")
        monkeypatch.setenv("WEBHOOK_HOST", "https://example.com/")
        monkeypatch.setenv("WEBHOOK_PATH", "/tg/abc123")
        monkeypatch.setenv("WEBHOOK_SECRET", "a" * 32)
        s = Settings()
        assert s.webhook_url() == "https://example.com/tg/abc123"


class TestLLMValidation:
    def test_ollama_does_not_require_api_key(
        self, env: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "ollama")
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        s = Settings()
        assert s.llm_provider is LLMProvider.OLLAMA
        assert s.llm_model == "gemma3:12b"

    def test_openai_without_key_rejected(
        self, env: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        with pytest.raises(ValueError, match="LLM_API_KEY is required"):
            Settings()


class TestQuietHours:
    def test_equal_hours_rejected(
        self, env: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("QUIET_FROM_HOUR", "10")
        monkeypatch.setenv("QUIET_TO_HOUR", "10")
        with pytest.raises(ValueError, match="must differ"):
            Settings()

    def test_out_of_range_rejected(
        self, env: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("QUIET_FROM_HOUR", "24")
        with pytest.raises(ValueError):
            Settings()


class TestCSVParsing:
    def test_allowed_hosts_default(self, env: dict[str, str]) -> None:
        s = Settings()
        assert s.allowed_hosts == ["*"]

    def test_allowed_hosts_csv(self, env: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ALLOWED_HOSTS", "example.com, api.example.com ,  ")
        s = Settings()
        assert s.allowed_hosts == ["example.com", "api.example.com"]

    def test_cors_origins_empty_string_is_empty_list(
        self, env: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CORS_ORIGINS", "")
        s = Settings()
        assert s.cors_origins == []


class TestLogLevel:
    @pytest.mark.parametrize("raw,expected", [("info", "INFO"), ("DEBUG", "DEBUG")])
    def test_normalised_to_upper(
        self, env: dict[str, str], monkeypatch: pytest.MonkeyPatch, raw: str, expected: str
    ) -> None:
        monkeypatch.setenv("LOG_LEVEL", raw)
        s = Settings()
        assert s.log_level == expected

    def test_invalid_rejected(self, env: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LOG_LEVEL", "verbose")
        with pytest.raises(ValueError, match="LOG_LEVEL must be one of"):
            Settings()


class TestGetSettingsCache:
    def test_returns_same_instance(self, env: dict[str, str]) -> None:
        assert get_settings() is get_settings()

    def test_cache_clear_returns_fresh(self, env: dict[str, str]) -> None:
        first = get_settings()
        get_settings.cache_clear()
        second = get_settings()
        assert first is not second


class TestIsDev:
    def test_default_allows_any_host_marked_dev(self, env: dict[str, str]) -> None:
        assert Settings().is_dev is True

    def test_specific_host_not_dev(
        self, env: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALLOWED_HOSTS", "callendulla.example.com")
        assert Settings().is_dev is False
