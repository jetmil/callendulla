# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""``UserMiddleware`` tests against an in-memory SQLite database.

We construct an Update by hand rather than going through aiogram's
parsing pipeline — that lets the test stay focused on the middleware's
behaviour without TG-bot wire-shape concerns.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Chat, Message, Update, User as TgUser
from cryptography.fernet import Fernet
from sqlalchemy import select

from callendulla.bot.middleware.user import UserMiddleware, _generate_ical_token
from callendulla.config import RegistrationMode, Settings, get_settings
from callendulla.db import Base
from callendulla.db.models import User, UserRole
from callendulla.db.session import create_engine, create_session_factory

_FERNET_KEY = Fernet.generate_key().decode()


@pytest.fixture
async def session_factory() -> AsyncIterator[object]:
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1234567890:AAFAKEFAKEFAKEFAKEFAKEFAKE00000000")
    monkeypatch.setenv("OWNER_TG_ID", "42")
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("LLM_API_KEY", "AIzaSyFAKEFAKEFAKEFAKEFAKEFAKEFAKE000")
    monkeypatch.setenv("SECRET_KEY", "a" * 32)
    monkeypatch.setenv("DIARY_ENCRYPTION_KEY", _FERNET_KEY)
    monkeypatch.setenv("REGISTRATION_MODE", "open")
    monkeypatch.chdir("/tmp")
    get_settings.cache_clear()


def _build_update(*, tg_id: int, username: str | None = None) -> Update:
    return Update(
        update_id=1,
        message=Message(
            message_id=1,
            date=0,
            chat=Chat(id=tg_id, type="private"),
            from_user=TgUser(id=tg_id, is_bot=False, first_name="Test", username=username),
            text="/start",
        ),
    )


class TestRegistrationOpen:
    async def test_creates_user_with_member_role(self, env: None, session_factory: object) -> None:
        settings = Settings()  # type: ignore[call-arg]
        mw = UserMiddleware(session_factory, settings)  # type: ignore[arg-type]
        handler = AsyncMock()
        data: dict[str, object] = {}

        await mw(handler, _build_update(tg_id=100, username="alice"), data)

        async with session_factory() as session:  # type: ignore[operator]
            users = list((await session.execute(select(User))).scalars())
        assert len(users) == 1
        u = users[0]
        assert u.tg_id == 100
        assert u.tg_username == "alice"
        assert u.role is UserRole.MEMBER
        assert data["user"].tg_id == 100  # type: ignore[union-attr]

    async def test_owner_id_promotes_role(self, env: None, session_factory: object) -> None:
        settings = Settings()  # type: ignore[call-arg]
        mw = UserMiddleware(session_factory, settings)  # type: ignore[arg-type]
        handler = AsyncMock()
        data: dict[str, object] = {}

        await mw(handler, _build_update(tg_id=42, username="owner"), data)

        assert data["user"].role is UserRole.OWNER  # type: ignore[union-attr]

    async def test_existing_member_with_owner_id_gets_promoted(
        self, env: None, session_factory: object
    ) -> None:
        """If OWNER_TG_ID changes to a user who's already a member,
        next interaction promotes them."""
        # Seed: existing member with tg_id == owner id but MEMBER role
        async with session_factory() as session:  # type: ignore[operator]
            session.add(
                User(
                    tg_id=42,
                    role=UserRole.MEMBER,
                    ical_token=_generate_ical_token(),
                )
            )
            await session.commit()

        settings = Settings()  # type: ignore[call-arg]
        mw = UserMiddleware(session_factory, settings)  # type: ignore[arg-type]
        await mw(AsyncMock(), _build_update(tg_id=42, username="owner"), {})

        async with session_factory() as session:  # type: ignore[operator]
            u = (await session.execute(select(User).where(User.tg_id == 42))).scalar_one()
        assert u.role is UserRole.OWNER


class TestRegistrationInvite:
    async def test_first_contact_user_refused(
        self,
        env: None,
        session_factory: object,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("REGISTRATION_MODE", "invite")
        get_settings.cache_clear()
        settings = Settings()  # type: ignore[call-arg]
        mw = UserMiddleware(session_factory, settings)  # type: ignore[arg-type]
        data: dict[str, object] = {}

        await mw(AsyncMock(), _build_update(tg_id=999, username="random"), data)

        assert data["user"] is None
        async with session_factory() as session:  # type: ignore[operator]
            users = list((await session.execute(select(User))).scalars())
        assert users == []

    async def test_owner_bypasses_invite(
        self,
        env: None,
        session_factory: object,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Owner can always self-register — otherwise we have a bootstrap problem."""
        monkeypatch.setenv("REGISTRATION_MODE", "invite")
        get_settings.cache_clear()
        settings = Settings()  # type: ignore[call-arg]
        mw = UserMiddleware(session_factory, settings)  # type: ignore[arg-type]
        data: dict[str, object] = {}

        await mw(AsyncMock(), _build_update(tg_id=42, username="owner"), data)

        assert data["user"] is not None
        assert data["user"].role is UserRole.OWNER  # type: ignore[union-attr]


class TestRegistrationWhitelist:
    async def test_username_in_whitelist_accepted(
        self,
        env: None,
        session_factory: object,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("REGISTRATION_MODE", "whitelist")
        monkeypatch.setenv("WHITELIST_TG_USERNAMES", "alice, bob")
        get_settings.cache_clear()
        settings = Settings()  # type: ignore[call-arg]
        assert settings.registration_mode is RegistrationMode.WHITELIST
        mw = UserMiddleware(session_factory, settings)  # type: ignore[arg-type]
        data: dict[str, object] = {}

        await mw(AsyncMock(), _build_update(tg_id=200, username="alice"), data)

        assert data["user"] is not None
        assert data["user"].tg_username == "alice"  # type: ignore[union-attr]

    async def test_username_not_in_whitelist_refused(
        self,
        env: None,
        session_factory: object,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("REGISTRATION_MODE", "whitelist")
        monkeypatch.setenv("WHITELIST_TG_USERNAMES", "alice, bob")
        get_settings.cache_clear()
        settings = Settings()  # type: ignore[call-arg]
        mw = UserMiddleware(session_factory, settings)  # type: ignore[arg-type]
        data: dict[str, object] = {}

        await mw(AsyncMock(), _build_update(tg_id=300, username="charlie"), data)

        assert data["user"] is None


class TestEventsWithoutUser:
    async def test_update_without_from_user_passes_through(
        self,
        env: None,
        session_factory: object,
    ) -> None:
        """An Update lacking a from_user (e.g. channel_post) still calls
        the inner handler with ``user=None`` rather than raising."""
        settings = Settings()  # type: ignore[call-arg]
        mw = UserMiddleware(session_factory, settings)  # type: ignore[arg-type]
        handler = AsyncMock()
        empty_update = Update(update_id=2)
        data: dict[str, object] = {}

        await mw(handler, empty_update, data)

        handler.assert_awaited_once()
        assert data["user"] is None
