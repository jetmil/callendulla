# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Voice-diary bot handler tests.

Stubs aiogram :class:`Bot` for file download + send_voice. Goal here
is to verify cross-user isolation and the end-to-end encrypt → store
→ decrypt → return cycle without going near a real Telegram or
filesystem.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.filters import CommandObject
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import select

from callendulla.bot.handlers.diary import (
    handle_diary,
    handle_voice,
)
from callendulla.core.voice_crypto import decrypt, encrypt
from callendulla.db import Base
from callendulla.db.models import User, VoiceDiary
from callendulla.db.session import create_engine, create_session_factory


@pytest.fixture
def fernet_key() -> SecretStr:
    return SecretStr(Fernet.generate_key().decode())


@pytest.fixture
def settings_stub(fernet_key: SecretStr) -> MagicMock:
    s = MagicMock()
    s.diary_encryption_key = fernet_key
    return s


@pytest.fixture
async def session_factory() -> AsyncIterator[object]:
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield create_session_factory(engine)
    await engine.dispose()


async def _seed_user(session_factory: object, *, tg_id: int, ical_token: str) -> User:
    async with session_factory() as session:  # type: ignore[operator]
        u = User(tg_id=tg_id, ical_token=ical_token, timezone="Europe/Moscow")
        session.add(u)
        await session.commit()
        await session.refresh(u)
        return u


def _mock_voice_message(voice_payload: bytes) -> tuple[MagicMock, MagicMock]:
    """Return (Message, Bot) — Bot.download_file returns the bytes."""
    msg = MagicMock()
    msg.reply = AsyncMock()
    msg.answer = AsyncMock()
    msg.chat.id = 1001

    voice = MagicMock()
    voice.file_id = "FAKEFILE"
    voice.duration = 5
    msg.voice = voice

    bot = MagicMock()
    file = MagicMock()
    file.file_path = "voice/fake.ogg"
    bot.get_file = AsyncMock(return_value=file)
    bot.download_file = AsyncMock(return_value=BytesIO(voice_payload))
    bot.send_voice = AsyncMock()
    return msg, bot


def _cmd(args: str | None) -> CommandObject:
    return CommandObject(prefix="/", command="diary", args=args)


# ── Voice ingestion ────────────────────────────────────────────────


class TestHandleVoice:
    async def test_unregistered_user_silent(
        self, settings_stub: MagicMock, session_factory: object
    ) -> None:
        msg, bot = _mock_voice_message(b"audio-bytes")
        await handle_voice(msg, None, bot, settings_stub, session_factory)  # type: ignore[arg-type]
        # No reply was sent — we don't store audio from anonymous users
        msg.reply.assert_not_awaited()
        async with session_factory() as session:  # type: ignore[operator]
            rows = list((await session.execute(select(VoiceDiary))).scalars())
        assert rows == []

    async def test_persists_encrypted(
        self, settings_stub: MagicMock, session_factory: object, fernet_key: SecretStr
    ) -> None:
        user = await _seed_user(session_factory, tg_id=1, ical_token="t1")
        plaintext = b"hello-recorded-audio"
        msg, bot = _mock_voice_message(plaintext)

        await handle_voice(msg, user, bot, settings_stub, session_factory)  # type: ignore[arg-type]
        msg.reply.assert_awaited_once()

        async with session_factory() as session:  # type: ignore[operator]
            entries = list((await session.execute(select(VoiceDiary))).scalars())
        assert len(entries) == 1
        # The ciphertext is NOT the plaintext
        assert entries[0].audio_ciphertext != plaintext
        assert plaintext not in entries[0].audio_ciphertext
        # But decrypts back
        assert decrypt(entries[0].audio_ciphertext, key=fernet_key) == plaintext


# ── /diary list ─────────────────────────────────────────────────────


class TestList:
    async def test_unregistered_user_refused(
        self, settings_stub: MagicMock, session_factory: object
    ) -> None:
        msg = MagicMock()
        msg.answer = AsyncMock()
        bot = MagicMock()
        await handle_diary(msg, None, _cmd(None), bot, settings_stub, session_factory)  # type: ignore[arg-type]
        text = msg.answer.await_args.args[0]
        assert "/start" in text

    async def test_empty_list_shows_help(
        self, settings_stub: MagicMock, session_factory: object
    ) -> None:
        user = await _seed_user(session_factory, tg_id=1, ical_token="t")
        msg = MagicMock()
        msg.answer = AsyncMock()
        bot = MagicMock()
        await handle_diary(msg, user, _cmd(None), bot, settings_stub, session_factory)  # type: ignore[arg-type]
        text = msg.answer.await_args.args[0]
        assert "Голосовой дневник" in text

    async def test_lists_own_entries_only(
        self,
        settings_stub: MagicMock,
        session_factory: object,
        fernet_key: SecretStr,
    ) -> None:
        alice = await _seed_user(session_factory, tg_id=1, ical_token="alice")
        bob = await _seed_user(session_factory, tg_id=2, ical_token="bob")
        async with session_factory() as session:  # type: ignore[operator]
            for owner in (alice, bob):
                session.add(
                    VoiceDiary(
                        owner_user_id=owner.id,
                        audio_ciphertext=encrypt(f"{owner.tg_id}-audio".encode(), key=fernet_key),
                        transcript_ciphertext=encrypt(b"", key=fernet_key),
                        duration_sec=3.0,
                    )
                )
            await session.commit()

        msg = MagicMock()
        msg.answer = AsyncMock()
        bot = MagicMock()
        await handle_diary(msg, alice, _cmd(None), bot, settings_stub, session_factory)  # type: ignore[arg-type]

        text = msg.answer.await_args.args[0]
        # Each entry line has format "#id · date · duration" — there
        # should be exactly one "#" entry header for alice
        assert text.count("\n#") == 1


# ── /diary play ────────────────────────────────────────────────────


class TestPlay:
    async def test_decrypts_and_sends(
        self,
        settings_stub: MagicMock,
        session_factory: object,
        fernet_key: SecretStr,
    ) -> None:
        user = await _seed_user(session_factory, tg_id=1, ical_token="t")
        plaintext = b"my-recorded-thoughts"
        async with session_factory() as session:  # type: ignore[operator]
            entry = VoiceDiary(
                owner_user_id=user.id,
                audio_ciphertext=encrypt(plaintext, key=fernet_key),
                transcript_ciphertext=encrypt(b"", key=fernet_key),
            )
            session.add(entry)
            await session.commit()
            await session.refresh(entry)
            entry_id = entry.id

        msg = MagicMock()
        msg.answer = AsyncMock()
        msg.chat.id = 1001
        bot = MagicMock()
        bot.send_voice = AsyncMock()
        await handle_diary(msg, user, _cmd(f"play {entry_id}"), bot, settings_stub, session_factory)  # type: ignore[arg-type]

        bot.send_voice.assert_awaited_once()
        kwargs = bot.send_voice.await_args.kwargs
        # The voice payload that hit send_voice is the decrypted bytes.
        # BufferedInputFile holds them; check its file attribute.
        voice_file = kwargs["voice"]
        # Aiogram's BufferedInputFile keeps payload in .data
        assert getattr(voice_file, "data", None) == plaintext

    async def test_other_users_entry_refused_same_text(
        self,
        settings_stub: MagicMock,
        session_factory: object,
        fernet_key: SecretStr,
    ) -> None:
        alice = await _seed_user(session_factory, tg_id=1, ical_token="alice")
        bob = await _seed_user(session_factory, tg_id=2, ical_token="bob")
        async with session_factory() as session:  # type: ignore[operator]
            alice_entry = VoiceDiary(
                owner_user_id=alice.id,
                audio_ciphertext=encrypt(b"alice-secret", key=fernet_key),
                transcript_ciphertext=encrypt(b"", key=fernet_key),
            )
            session.add(alice_entry)
            await session.commit()
            await session.refresh(alice_entry)
            alice_entry_id = alice_entry.id

        msg = MagicMock()
        msg.answer = AsyncMock()
        bot = MagicMock()
        bot.send_voice = AsyncMock()
        await handle_diary(
            msg, bob, _cmd(f"play {alice_entry_id}"), bot, settings_stub, session_factory
        )  # type: ignore[arg-type]

        # No send_voice — no leak of alice's content.
        bot.send_voice.assert_not_awaited()
        text = msg.answer.await_args.args[0]
        assert "не найдена" in text.lower()
        assert "alice-secret" not in text


# ── /diary forget ──────────────────────────────────────────────────


class TestForget:
    async def test_deletes_own_entry(
        self,
        settings_stub: MagicMock,
        session_factory: object,
        fernet_key: SecretStr,
    ) -> None:
        user = await _seed_user(session_factory, tg_id=1, ical_token="t")
        async with session_factory() as session:  # type: ignore[operator]
            entry = VoiceDiary(
                owner_user_id=user.id,
                audio_ciphertext=encrypt(b"x", key=fernet_key),
                transcript_ciphertext=encrypt(b"", key=fernet_key),
            )
            session.add(entry)
            await session.commit()
            await session.refresh(entry)
            entry_id = entry.id

        msg = MagicMock()
        msg.answer = AsyncMock()
        bot = MagicMock()
        await handle_diary(
            msg, user, _cmd(f"forget {entry_id}"), bot, settings_stub, session_factory
        )  # type: ignore[arg-type]

        async with session_factory() as session:  # type: ignore[operator]
            remaining = list((await session.execute(select(VoiceDiary))).scalars())
        assert remaining == []

    async def test_other_users_entry_refused(
        self,
        settings_stub: MagicMock,
        session_factory: object,
        fernet_key: SecretStr,
    ) -> None:
        alice = await _seed_user(session_factory, tg_id=1, ical_token="alice")
        bob = await _seed_user(session_factory, tg_id=2, ical_token="bob")
        async with session_factory() as session:  # type: ignore[operator]
            alice_entry = VoiceDiary(
                owner_user_id=alice.id,
                audio_ciphertext=encrypt(b"alice", key=fernet_key),
                transcript_ciphertext=encrypt(b"", key=fernet_key),
            )
            session.add(alice_entry)
            await session.commit()
            await session.refresh(alice_entry)
            alice_id = alice_entry.id

        msg = MagicMock()
        msg.answer = AsyncMock()
        bot = MagicMock()
        await handle_diary(
            msg, bob, _cmd(f"forget {alice_id}"), bot, settings_stub, session_factory
        )  # type: ignore[arg-type]

        # Alice's entry still there.
        async with session_factory() as session:  # type: ignore[operator]
            rows = list((await session.execute(select(VoiceDiary))).scalars())
        assert len(rows) == 1
        assert rows[0].owner_user_id == alice.id
