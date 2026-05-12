# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""STT adapter + factory + diary transcription background task tests.

Every test mocks the SDK. CI never makes a real API call.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import select

from callendulla.bot.handlers.diary import _transcribe_and_persist
from callendulla.config import Settings, get_settings
from callendulla.core.voice_crypto import decrypt, encrypt
from callendulla.db import Base
from callendulla.db.models import User, VoiceDiary
from callendulla.db.session import create_engine, create_session_factory
from callendulla.stt.base import STTError
from callendulla.stt.factory import build_stt_provider
from callendulla.stt.openai_whisper import OpenAIWhisperProvider

_FERNET_KEY = Fernet.generate_key().decode()
_SECRET_32 = "a" * 32


def _env(provider: str = "openai", *, with_key: bool = True) -> dict[str, str]:
    base = {
        "TELEGRAM_BOT_TOKEN": "1234567890:AAFAKEFAKEFAKEFAKEFAKEFAKE00000000",
        "OWNER_TG_ID": "42",
        "LLM_PROVIDER": provider,
        "SECRET_KEY": _SECRET_32,
        "DIARY_ENCRYPTION_KEY": _FERNET_KEY,
    }
    if with_key:
        base["LLM_API_KEY"] = "FAKE-NEVER-USED"
    return base


@pytest.fixture(autouse=True)
def _stub_openai_sdk() -> Iterator[None]:
    """Stub the openai SDK so the lazy import inside the provider works."""
    fake = MagicMock()
    fake.AsyncOpenAI = MagicMock()
    with patch.dict("sys.modules", {"openai": fake}):
        yield


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
    ):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)


# ── Provider adapter ────────────────────────────────────────────────


def _fake_whisper_response(text: str) -> Any:
    resp = MagicMock()
    resp.text = text
    return resp


class TestOpenAIWhisper:
    async def test_happy_path(self) -> None:
        provider = OpenAIWhisperProvider(api_key="K")
        client = MagicMock()
        client.audio.transcriptions.create = AsyncMock(
            return_value=_fake_whisper_response("привет это тест")
        )
        provider._client = client  # type: ignore[attr-defined]

        text = await provider.transcribe(b"\x00" * 100, fmt="ogg")
        assert text == "привет это тест"

    async def test_sdk_exception_becomes_stt_error(self) -> None:
        provider = OpenAIWhisperProvider(api_key="K")
        client = MagicMock()
        client.audio.transcriptions.create = AsyncMock(side_effect=RuntimeError("net"))
        provider._client = client  # type: ignore[attr-defined]
        with pytest.raises(STTError):
            await provider.transcribe(b"\x00")

    async def test_empty_text_becomes_stt_error(self) -> None:
        provider = OpenAIWhisperProvider(api_key="K")
        client = MagicMock()
        client.audio.transcriptions.create = AsyncMock(return_value=_fake_whisper_response(""))
        provider._client = client  # type: ignore[attr-defined]
        with pytest.raises(STTError):
            await provider.transcribe(b"\x00")

    async def test_language_forwarded(self) -> None:
        provider = OpenAIWhisperProvider(api_key="K")
        client = MagicMock()
        client.audio.transcriptions.create = AsyncMock(return_value=_fake_whisper_response("ok"))
        provider._client = client  # type: ignore[attr-defined]
        await provider.transcribe(b"\x00", language="ru")
        kwargs = client.audio.transcriptions.create.await_args.kwargs
        assert kwargs.get("language") == "ru"

    def test_repr_does_not_leak_api_key(self) -> None:
        text = repr(OpenAIWhisperProvider(api_key="SECRET-KEY-XYZ"))
        assert "SECRET-KEY-XYZ" not in text


# ── Factory ─────────────────────────────────────────────────────────


class TestFactory:
    def test_openai_returns_whisper(self, env_setter: pytest.MonkeyPatch) -> None:
        _apply_env(env_setter, _env("openai"))
        settings = Settings()  # type: ignore[call-arg]
        provider = build_stt_provider(settings)
        assert provider is not None
        assert type(provider).__name__ == "OpenAIWhisperProvider"

    @pytest.mark.parametrize("kind", ["gemini", "anthropic", "ollama"])
    def test_other_providers_return_none(self, env_setter: pytest.MonkeyPatch, kind: str) -> None:
        _apply_env(env_setter, _env(kind, with_key=kind != "ollama"))
        settings = Settings()  # type: ignore[call-arg]
        assert build_stt_provider(settings) is None

    def test_uses_operator_key(self, env_setter: pytest.MonkeyPatch) -> None:
        env = _env("openai")
        env["LLM_API_KEY"] = "operator-private-key-abc"
        _apply_env(env_setter, env)
        settings = Settings()  # type: ignore[call-arg]
        provider = build_stt_provider(settings)
        assert provider is not None
        assert provider._api_key == "operator-private-key-abc"


# ── Background transcription task ──────────────────────────────────


@pytest.fixture
async def session_factory() -> AsyncIterator[object]:
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture
def fernet_key() -> SecretStr:
    return SecretStr(Fernet.generate_key().decode())


@pytest.fixture
def settings_stub(fernet_key: SecretStr) -> MagicMock:
    s = MagicMock()
    s.diary_encryption_key = fernet_key
    return s


async def _seed_entry(session_factory: object, fernet_key: SecretStr) -> tuple[User, int]:
    async with session_factory() as session:  # type: ignore[operator]
        user = User(tg_id=1, ical_token="t", timezone="Europe/Moscow")
        session.add(user)
        await session.flush()
        entry = VoiceDiary(
            owner_user_id=user.id,
            audio_ciphertext=encrypt(b"audio", key=fernet_key),
            transcript_ciphertext=encrypt(b"", key=fernet_key),  # empty placeholder
        )
        session.add(entry)
        await session.commit()
        await session.refresh(user)
        await session.refresh(entry)
        return user, entry.id


class TestBackgroundTranscription:
    async def test_persists_encrypted_transcript(
        self,
        session_factory: object,
        fernet_key: SecretStr,
        settings_stub: MagicMock,
    ) -> None:
        _user, entry_id = await _seed_entry(session_factory, fernet_key)
        bot = MagicMock()
        bot.send_message = AsyncMock()
        stt = MagicMock()
        stt.transcribe = AsyncMock(return_value="расшифровка готова")

        await _transcribe_and_persist(
            bot=bot,
            chat_id=1001,
            entry_id=entry_id,
            audio_plain=b"raw-audio-bytes",
            stt=stt,
            settings=settings_stub,
            session_factory=session_factory,  # type: ignore[arg-type]
        )

        async with session_factory() as session:  # type: ignore[operator]
            entry = (
                await session.execute(select(VoiceDiary).where(VoiceDiary.id == entry_id))
            ).scalar_one()
        # Encrypted under the diary key, decrypts to the transcript text
        plain = decrypt(entry.transcript_ciphertext, key=fernet_key).decode("utf-8")
        assert plain == "расшифровка готова"
        # User got the follow-up notification
        bot.send_message.assert_awaited_once()

    async def test_stt_error_leaves_placeholder(
        self,
        session_factory: object,
        fernet_key: SecretStr,
        settings_stub: MagicMock,
    ) -> None:
        _user, entry_id = await _seed_entry(session_factory, fernet_key)
        bot = MagicMock()
        bot.send_message = AsyncMock()
        stt = MagicMock()
        stt.transcribe = AsyncMock(side_effect=STTError("quota"))

        await _transcribe_and_persist(
            bot=bot,
            chat_id=1001,
            entry_id=entry_id,
            audio_plain=b"raw-audio-bytes",
            stt=stt,
            settings=settings_stub,
            session_factory=session_factory,  # type: ignore[arg-type]
        )

        # Transcript stays as the empty placeholder; user not notified.
        async with session_factory() as session:  # type: ignore[operator]
            entry = (
                await session.execute(select(VoiceDiary).where(VoiceDiary.id == entry_id))
            ).scalar_one()
        plain = decrypt(entry.transcript_ciphertext, key=fernet_key)
        assert plain == b""
        bot.send_message.assert_not_awaited()

    async def test_forgotten_entry_drops_transcript(
        self,
        session_factory: object,
        fernet_key: SecretStr,
        settings_stub: MagicMock,
    ) -> None:
        """User /diary forgot the entry between voice arrival and the
        STT task finishing. The task must NOT recreate the row."""
        _user, entry_id = await _seed_entry(session_factory, fernet_key)
        # Forget it
        async with session_factory() as session:  # type: ignore[operator]
            entry = (
                await session.execute(select(VoiceDiary).where(VoiceDiary.id == entry_id))
            ).scalar_one()
            await session.delete(entry)
            await session.commit()

        bot = MagicMock()
        bot.send_message = AsyncMock()
        stt = MagicMock()
        stt.transcribe = AsyncMock(return_value="ghost transcript")

        await _transcribe_and_persist(
            bot=bot,
            chat_id=1001,
            entry_id=entry_id,
            audio_plain=b"raw-audio-bytes",
            stt=stt,
            settings=settings_stub,
            session_factory=session_factory,  # type: ignore[arg-type]
        )

        async with session_factory() as session:  # type: ignore[operator]
            rows = list((await session.execute(select(VoiceDiary))).scalars())
        assert rows == []  # nothing was resurrected
        # No follow-up notification either
        bot.send_message.assert_not_awaited()
