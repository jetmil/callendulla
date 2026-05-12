# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Tests for :func:`build_provider`.

We stub the SDK imports so this runs with no network and no real keys.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet

from callendulla.config import LLMProvider as ProviderKind, Settings, get_settings
from callendulla.llm.factory import build_provider

_FERNET_KEY = Fernet.generate_key().decode()
_SECRET_32 = "a" * 32


def _env(provider: str, with_key: bool = True) -> dict[str, str]:
    base = {
        "TELEGRAM_BOT_TOKEN": "1234567890:AAFAKEFAKEFAKEFAKEFAKEFAKE00000000",
        "OWNER_TG_ID": "42",
        "LLM_PROVIDER": provider,
        "SECRET_KEY": _SECRET_32,
        "DIARY_ENCRYPTION_KEY": _FERNET_KEY,
    }
    if with_key:
        base["LLM_API_KEY"] = "FAKE-KEY-NEVER-USED"
    return base


@pytest.fixture(autouse=True)
def _stub_sdk_imports() -> Iterator[None]:
    """Replace every vendor SDK with a MagicMock at the module path the
    factory imports from. The factory uses lazy ``from x import Y``
    inside the function, so we patch the *attribute access* shape
    (``patch.dict`` on ``sys.modules``) to provide fake modules.
    """
    fake_modules: dict[str, Any] = {
        "google": MagicMock(),
        "google.genai": MagicMock(),
        "openai": MagicMock(),
        "anthropic": MagicMock(),
        "ollama": MagicMock(),
    }
    # Make ``from google import genai`` return our fake.
    fake_modules["google"].genai = fake_modules["google.genai"]
    with patch.dict("sys.modules", fake_modules):
        yield


@pytest.fixture
def env_setter(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    monkeypatch.chdir("/tmp")
    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()


def _apply_env(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> None:
    for key in (
        "TELEGRAM_BOT_TOKEN",
        "OWNER_TG_ID",
        "LLM_PROVIDER",
        "LLM_API_KEY",
        "LLM_MODEL",
        "SECRET_KEY",
        "DIARY_ENCRYPTION_KEY",
        "OLLAMA_BASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)


class TestProviderPicks:
    def test_gemini(self, env_setter: pytest.MonkeyPatch) -> None:
        _apply_env(env_setter, _env("gemini"))
        provider = build_provider(Settings())  # type: ignore[call-arg]
        assert type(provider).__name__ == "GeminiProvider"

    def test_openai(self, env_setter: pytest.MonkeyPatch) -> None:
        _apply_env(env_setter, _env("openai"))
        provider = build_provider(Settings())  # type: ignore[call-arg]
        assert type(provider).__name__ == "OpenAIProvider"

    def test_anthropic(self, env_setter: pytest.MonkeyPatch) -> None:
        _apply_env(env_setter, _env("anthropic"))
        provider = build_provider(Settings())  # type: ignore[call-arg]
        assert type(provider).__name__ == "AnthropicProvider"

    def test_ollama_does_not_require_key(self, env_setter: pytest.MonkeyPatch) -> None:
        _apply_env(env_setter, _env("ollama", with_key=False))
        provider = build_provider(Settings())  # type: ignore[call-arg]
        assert type(provider).__name__ == "OllamaProvider"


class TestKeyRequirement:
    @pytest.mark.parametrize("kind", ["gemini", "openai", "anthropic"])
    def test_missing_key_raises_at_settings_layer(
        self,
        env_setter: pytest.MonkeyPatch,
        kind: str,
    ) -> None:
        """Settings already rejects missing LLM_API_KEY for non-ollama
        providers in PR2. Factory therefore never sees the bad state —
        belt-and-braces test ensures this stays true."""
        _apply_env(env_setter, _env(kind, with_key=False))
        with pytest.raises(ValueError, match="LLM_API_KEY is required"):
            Settings()  # type: ignore[call-arg]


class TestProviderUsesKeyFromSettings:
    """The factory MUST pass the operator's key into the provider — and
    nothing else. No hard-coded key, no env-bypass, no default."""

    def test_gemini_uses_settings_key(self, env_setter: pytest.MonkeyPatch) -> None:
        env = _env("gemini")
        env["LLM_API_KEY"] = "operator-provided-key-xyz"
        _apply_env(env_setter, env)
        provider = build_provider(Settings())  # type: ignore[call-arg]
        # _api_key is a private attribute; we read it just for this test
        assert provider._api_key == "operator-provided-key-xyz"

    def test_openai_uses_settings_key(self, env_setter: pytest.MonkeyPatch) -> None:
        env = _env("openai")
        env["LLM_API_KEY"] = "operator-provided-key-abc"
        _apply_env(env_setter, env)
        provider = build_provider(Settings())  # type: ignore[call-arg]
        assert provider._api_key == "operator-provided-key-abc"

    def test_anthropic_uses_settings_key(self, env_setter: pytest.MonkeyPatch) -> None:
        env = _env("anthropic")
        env["LLM_API_KEY"] = "operator-provided-key-def"
        _apply_env(env_setter, env)
        provider = build_provider(Settings())  # type: ignore[call-arg]
        assert provider._api_key == "operator-provided-key-def"


# Unused import suppression — ProviderKind is here for any downstream
# tests that want to assert on enum values.
_ = ProviderKind
