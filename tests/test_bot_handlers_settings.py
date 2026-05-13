# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""``/voice``, ``/timezone``, ``/quiet`` handler tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.filters import CommandObject
from sqlalchemy import select

from callendulla.bot.handlers.settings import (
    handle_quiet,
    handle_timezone,
    handle_voice,
)
from callendulla.db import Base
from callendulla.db.models import User, VoiceProfile
from callendulla.db.session import create_engine, create_session_factory


@pytest.fixture
async def session_factory() -> AsyncIterator[object]:
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield create_session_factory(engine)
    await engine.dispose()


async def _seed_user(session_factory: object) -> User:
    async with session_factory() as session:  # type: ignore[operator]
        u = User(
            tg_id=1001,
            ical_token="t1",
            timezone="Europe/Moscow",
            voice_profile=VoiceProfile.WARM_SISTER,
            quiet_from_hour=22,
            quiet_to_hour=9,
        )
        session.add(u)
        await session.commit()
        await session.refresh(u)
        return u


def _mock_message() -> MagicMock:
    msg = MagicMock()
    msg.answer = AsyncMock()
    return msg


def _cmd(args: str | None) -> CommandObject:
    return CommandObject(prefix="/", command="x", args=args)


async def _reload(session_factory: object, user_id: int) -> User:
    async with session_factory() as session:  # type: ignore[operator]
        return (await session.execute(select(User).where(User.id == user_id))).scalar_one()


# ── /voice ─────────────────────────────────────────────────────────


class TestVoice:
    async def test_unregistered_refused(self, session_factory: object) -> None:
        msg = _mock_message()
        await handle_voice(msg, None, _cmd("warm_sister"), session_factory)  # type: ignore[arg-type]
        assert "/start" in msg.answer.await_args.args[0]

    async def test_no_args_shows_current_and_list(self, session_factory: object) -> None:
        user = await _seed_user(session_factory)
        msg = _mock_message()
        await handle_voice(msg, user, _cmd(None), session_factory)  # type: ignore[arg-type]
        text = msg.answer.await_args.args[0]
        assert "warm_sister" in text
        assert "brutal_bro" in text  # full list shown

    async def test_invalid_profile_rejected(self, session_factory: object) -> None:
        user = await _seed_user(session_factory)
        msg = _mock_message()
        await handle_voice(msg, user, _cmd("totally_made_up"), session_factory)  # type: ignore[arg-type]
        text = msg.answer.await_args.args[0]
        assert "totally_made_up" in text
        # Was not persisted
        fresh = await _reload(session_factory, user.id)
        assert fresh.voice_profile is VoiceProfile.WARM_SISTER

    async def test_valid_profile_persisted(self, session_factory: object) -> None:
        user = await _seed_user(session_factory)
        msg = _mock_message()
        await handle_voice(msg, user, _cmd("drill_sergeant"), session_factory)  # type: ignore[arg-type]
        fresh = await _reload(session_factory, user.id)
        assert fresh.voice_profile is VoiceProfile.DRILL_SERGEANT


# ── /timezone ──────────────────────────────────────────────────────


class TestTimezone:
    async def test_unregistered_refused(self, session_factory: object) -> None:
        msg = _mock_message()
        await handle_timezone(msg, None, _cmd("Europe/Berlin"), session_factory)  # type: ignore[arg-type]
        assert "/start" in msg.answer.await_args.args[0]

    async def test_no_args_shows_current(self, session_factory: object) -> None:
        user = await _seed_user(session_factory)
        msg = _mock_message()
        await handle_timezone(msg, user, _cmd(None), session_factory)  # type: ignore[arg-type]
        text = msg.answer.await_args.args[0]
        assert "Europe/Moscow" in text

    async def test_unknown_zone_rejected(self, session_factory: object) -> None:
        user = await _seed_user(session_factory)
        msg = _mock_message()
        await handle_timezone(msg, user, _cmd("Atlantis/Mu"), session_factory)  # type: ignore[arg-type]
        text = msg.answer.await_args.args[0]
        assert "Atlantis/Mu" in text
        fresh = await _reload(session_factory, user.id)
        assert fresh.timezone == "Europe/Moscow"

    async def test_valid_zone_persisted(self, session_factory: object) -> None:
        user = await _seed_user(session_factory)
        msg = _mock_message()
        await handle_timezone(msg, user, _cmd("Asia/Yekaterinburg"), session_factory)  # type: ignore[arg-type]
        fresh = await _reload(session_factory, user.id)
        assert fresh.timezone == "Asia/Yekaterinburg"


# ── /quiet ─────────────────────────────────────────────────────────


class TestQuiet:
    async def test_unregistered_refused(self, session_factory: object) -> None:
        msg = _mock_message()
        await handle_quiet(msg, None, _cmd("22 9"), session_factory)  # type: ignore[arg-type]
        assert "/start" in msg.answer.await_args.args[0]

    async def test_no_args_shows_current(self, session_factory: object) -> None:
        user = await _seed_user(session_factory)
        msg = _mock_message()
        await handle_quiet(msg, user, _cmd(None), session_factory)  # type: ignore[arg-type]
        text = msg.answer.await_args.args[0]
        assert "22:00" in text
        assert "09:00" in text

    async def test_bad_format_rejected(self, session_factory: object) -> None:
        user = await _seed_user(session_factory)
        msg = _mock_message()
        await handle_quiet(msg, user, _cmd("ten elev"), session_factory)  # type: ignore[arg-type]
        assert "Формат" in msg.answer.await_args.args[0]

    async def test_out_of_range_rejected(self, session_factory: object) -> None:
        user = await _seed_user(session_factory)
        msg = _mock_message()
        await handle_quiet(msg, user, _cmd("25 9"), session_factory)  # type: ignore[arg-type]
        assert "0..23" in msg.answer.await_args.args[0]

    async def test_equal_hours_rejected(self, session_factory: object) -> None:
        user = await _seed_user(session_factory)
        msg = _mock_message()
        await handle_quiet(msg, user, _cmd("10 10"), session_factory)  # type: ignore[arg-type]
        assert "24/7" in msg.answer.await_args.args[0]
        fresh = await _reload(session_factory, user.id)
        assert (fresh.quiet_from_hour, fresh.quiet_to_hour) == (22, 9)

    async def test_valid_quiet_window_persisted(self, session_factory: object) -> None:
        user = await _seed_user(session_factory)
        msg = _mock_message()
        await handle_quiet(msg, user, _cmd("23 8"), session_factory)  # type: ignore[arg-type]
        fresh = await _reload(session_factory, user.id)
        assert fresh.quiet_from_hour == 23
        assert fresh.quiet_to_hour == 8
