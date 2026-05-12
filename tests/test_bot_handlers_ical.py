# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""``/ical`` and ``/rotate_ical`` bot handler tests."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from callendulla.bot.handlers.ical import handle_ical, handle_rotate_ical
from callendulla.config import Settings, get_settings
from callendulla.db import Base
from callendulla.db.models import User
from callendulla.db.session import create_engine, create_session_factory

_FERNET_KEY = Fernet.generate_key().decode()
_SECRET_32 = "a" * 32


def _env(*, web_base_url: str | None = "https://callendulla.example.com") -> dict[str, str]:
    base = {
        "TELEGRAM_BOT_TOKEN": "1234567890:AAFAKEFAKEFAKEFAKEFAKEFAKE00000000",
        "OWNER_TG_ID": "42",
        "LLM_PROVIDER": "gemini",
        "LLM_API_KEY": "AIzaSyFAKEFAKEFAKEFAKEFAKEFAKEFAKE000",
        "SECRET_KEY": _SECRET_32,
        "DIARY_ENCRYPTION_KEY": _FERNET_KEY,
    }
    if web_base_url is not None:
        base["WEB_BASE_URL"] = web_base_url
    return base


@pytest.fixture
async def session_factory() -> AsyncIterator[object]:
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for k in (*_env(), "WEB_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    for k, v in _env().items():
        monkeypatch.setenv(k, v)
    monkeypatch.chdir("/tmp")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def env_no_base_url(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Same env but WEB_BASE_URL absent — tests the "operator did not
    configure the public URL" path."""
    raw = _env(web_base_url=None)
    for k in (*raw, "WEB_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    for k, v in raw.items():
        monkeypatch.setenv(k, v)
    monkeypatch.chdir("/tmp")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _seed_user(session_factory: object) -> User:
    async with session_factory() as session:  # type: ignore[operator]
        u = User(
            tg_id=1001,
            ical_token="orig-token-do-not-leak-in-other-tests",
            timezone="Europe/Moscow",
        )
        session.add(u)
        await session.commit()
        await session.refresh(u)
        return u


def _mock_message() -> MagicMock:
    msg = MagicMock()
    msg.answer = AsyncMock()
    return msg


class TestIcalCommand:
    async def test_unregistered_user_refused(
        self,
        env: None,
        session_factory: object,
    ) -> None:
        msg = _mock_message()
        await handle_ical(msg, None, Settings())  # type: ignore[call-arg]
        text = msg.answer.await_args.args[0]
        assert "/start" in text

    async def test_returns_url(
        self,
        env: None,
        session_factory: object,
    ) -> None:
        user = await _seed_user(session_factory)
        msg = _mock_message()
        await handle_ical(msg, user, Settings())  # type: ignore[call-arg]
        text = msg.answer.await_args.args[0]
        assert "callendulla.example.com/ical/" in text
        assert user.ical_token in text

    async def test_no_base_url_explains(
        self,
        env_no_base_url: None,
        session_factory: object,
    ) -> None:
        user = await _seed_user(session_factory)
        msg = _mock_message()
        await handle_ical(msg, user, Settings())  # type: ignore[call-arg]
        text = msg.answer.await_args.args[0]
        assert "WEB_BASE_URL" in text


class TestRotateIcal:
    async def test_unregistered_user_refused(
        self,
        env: None,
        session_factory: object,
    ) -> None:
        msg = _mock_message()
        await handle_rotate_ical(msg, None, Settings(), session_factory)  # type: ignore[call-arg]
        text = msg.answer.await_args.args[0]
        assert "/start" in text

    async def test_rotates_and_returns_new_url(
        self,
        env: None,
        session_factory: object,
    ) -> None:
        user = await _seed_user(session_factory)
        old_token = user.ical_token

        msg = _mock_message()
        await handle_rotate_ical(msg, user, Settings(), session_factory)  # type: ignore[call-arg]
        text = msg.answer.await_args.args[0]
        assert "Токен обновлён" in text or "обновлён" in text.lower()

        async with session_factory() as session:  # type: ignore[operator]
            fresh = (await session.execute(select(User).where(User.id == user.id))).scalar_one()
        assert fresh.ical_token != old_token
        assert len(fresh.ical_token) == 32  # 16 bytes -> 32 hex chars
        # New URL contains the new token
        assert fresh.ical_token in text

    async def test_each_call_yields_distinct_token(
        self,
        env: None,
        session_factory: object,
    ) -> None:
        user = await _seed_user(session_factory)
        await handle_rotate_ical(_mock_message(), user, Settings(), session_factory)  # type: ignore[call-arg]
        async with session_factory() as session:  # type: ignore[operator]
            fresh = (await session.execute(select(User).where(User.id == user.id))).scalar_one()
            t1 = fresh.ical_token
            # Need to use the freshly-loaded user for the second call
            await handle_rotate_ical(_mock_message(), fresh, Settings(), session_factory)  # type: ignore[call-arg]
            await session.refresh(fresh)
            t2 = fresh.ical_token
        assert t1 != t2
