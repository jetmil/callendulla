# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Tests for the TTS layer.

Every test mocks edge-tts. Real Azure call is forbidden in CI — would
flake on rate limit, slow the suite, and pin behaviour to a third party.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet

from callendulla.config import Settings, TTSEngine, get_settings
from callendulla.tts.base import TTSError
from callendulla.tts.edge import EdgeTTSProvider
from callendulla.tts.factory import build_tts_provider

_FERNET_KEY = Fernet.generate_key().decode()
_SECRET_32 = "a" * 32


def _env(engine: str = "edge") -> dict[str, str]:
    return {
        "TELEGRAM_BOT_TOKEN": "1234567890:AAFAKEFAKEFAKEFAKEFAKEFAKE00000000",
        "OWNER_TG_ID": "42",
        "LLM_PROVIDER": "gemini",
        "LLM_API_KEY": "AIzaSyFAKEFAKEFAKEFAKEFAKEFAKEFAKE000",
        "SECRET_KEY": _SECRET_32,
        "DIARY_ENCRYPTION_KEY": _FERNET_KEY,
        "TTS_ENGINE": engine,
    }


@pytest.fixture
def env_setter(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    monkeypatch.chdir("/tmp")
    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()


def _apply_env(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> None:
    for k in (
        "TELEGRAM_BOT_TOKEN",
        "OWNER_TG_ID",
        "LLM_PROVIDER",
        "LLM_API_KEY",
        "LLM_MODEL",
        "SECRET_KEY",
        "DIARY_ENCRYPTION_KEY",
        "TTS_ENGINE",
    ):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)


# ── Factory ─────────────────────────────────────────────────────────


class TestFactory:
    def test_edge_returns_provider(self, env_setter: pytest.MonkeyPatch) -> None:
        _apply_env(env_setter, _env("edge"))
        settings = Settings()  # type: ignore[call-arg]
        provider = build_tts_provider(settings)
        assert provider is not None
        assert type(provider).__name__ == "EdgeTTSProvider"

    @pytest.mark.parametrize("engine", ["piper", "cosyvoice"])
    def test_unwired_engines_return_none(self, env_setter: pytest.MonkeyPatch, engine: str) -> None:
        """Piper and CosyVoice paths land in follow-up PRs; meanwhile
        their settings fall back to "no TTS" without breaking the bot."""
        _apply_env(env_setter, _env(engine))
        settings = Settings()  # type: ignore[call-arg]
        # We assert via the enum value since the type system enforces
        # the cast at Settings construction time.
        assert settings.tts_engine in (TTSEngine.PIPER, TTSEngine.COSYVOICE)
        assert build_tts_provider(settings) is None


# ── Adapter (mocked edge-tts) ───────────────────────────────────────


class _FakeStream:
    """Async-iterable that yields the chunks given at construction."""

    def __init__(self, chunks: list[dict[str, Any]]) -> None:
        self._chunks = chunks

    def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        return self._iterator()

    async def _iterator(self) -> AsyncIterator[dict[str, Any]]:
        for chunk in self._chunks:
            yield chunk


def _fake_edge_tts(
    chunks: list[dict[str, Any]] | None = None, *, raises: Exception | None = None
) -> MagicMock:
    """Stand-in for the ``edge_tts`` module with a Communicate class."""
    module = MagicMock()
    communicate = MagicMock()
    if raises is not None:
        communicate.stream.side_effect = raises
    else:
        communicate.stream.return_value = _FakeStream(chunks or [])
    module.Communicate = MagicMock(return_value=communicate)
    return module


class TestEdgeTTS:
    async def test_happy_path_concatenates_audio_chunks(self) -> None:
        fake = _fake_edge_tts(
            chunks=[
                {"type": "audio", "data": b"\x00\x01"},
                {"type": "WordBoundary"},  # ignored
                {"type": "audio", "data": b"\x02\x03"},
            ]
        )
        with patch.dict("sys.modules", {"edge_tts": fake}):
            provider = EdgeTTSProvider()
            audio = await provider.synthesize("hello")
        assert audio == b"\x00\x01\x02\x03"

    async def test_default_voice_used(self) -> None:
        fake = _fake_edge_tts(chunks=[{"type": "audio", "data": b"\x00"}])
        with patch.dict("sys.modules", {"edge_tts": fake}):
            provider = EdgeTTSProvider()
            await provider.synthesize("hi")
        # Communicate(text, voice) — second positional is the voice
        args = fake.Communicate.call_args.args
        assert args[1] == "ru-RU-SvetlanaNeural"

    async def test_voice_kwarg_overrides_default(self) -> None:
        fake = _fake_edge_tts(chunks=[{"type": "audio", "data": b"\x00"}])
        with patch.dict("sys.modules", {"edge_tts": fake}):
            provider = EdgeTTSProvider(voice="en-US-AriaNeural")
            await provider.synthesize("hi")
        assert fake.Communicate.call_args.args[1] == "en-US-AriaNeural"

    async def test_call_site_voice_overrides_init_voice(self) -> None:
        fake = _fake_edge_tts(chunks=[{"type": "audio", "data": b"\x00"}])
        with patch.dict("sys.modules", {"edge_tts": fake}):
            provider = EdgeTTSProvider(voice="ru-RU-SvetlanaNeural")
            await provider.synthesize("hi", voice="ru-RU-DmitryNeural")
        assert fake.Communicate.call_args.args[1] == "ru-RU-DmitryNeural"

    async def test_sdk_exception_becomes_tts_error(self) -> None:
        fake = _fake_edge_tts(raises=RuntimeError("net"))
        with patch.dict("sys.modules", {"edge_tts": fake}):
            provider = EdgeTTSProvider()
            with pytest.raises(TTSError):
                await provider.synthesize("hi")

    async def test_no_audio_chunks_becomes_tts_error(self) -> None:
        # WordBoundary-only stream → zero audio
        fake = _fake_edge_tts(chunks=[{"type": "WordBoundary"}])
        with patch.dict("sys.modules", {"edge_tts": fake}):
            provider = EdgeTTSProvider()
            with pytest.raises(TTSError):
                await provider.synthesize("hi")

    async def test_repr_does_not_carry_secrets(self) -> None:
        """EdgeTTS has no API key, but repr() should also not leak
        the configured voice in a misleading way. Defensive."""
        provider = EdgeTTSProvider(voice="ru-RU-SvetlanaNeural")
        # Default repr is fine; this is a guard against future refactors
        # adding a __repr__ that dumps state.
        assert "SECRET" not in repr(provider).upper()
