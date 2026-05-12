# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Unit tests for ``/start``, ``/source``, ``/help`` handlers.

We don't spin up a real :class:`aiogram.Dispatcher` here — every
handler is a pure async function that takes a :class:`types.Message`
and whatever the middleware injected. Mocking ``message.answer`` as an
:class:`AsyncMock` keeps the tests fast and free of Telegram-API
plumbing. The middleware itself is tested separately against an
in-memory SQLite database.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from cryptography.fernet import Fernet

from callendulla.bot.handlers.help import handle_help
from callendulla.bot.handlers.source import handle_source
from callendulla.bot.handlers.start import handle_start
from callendulla.config import Settings, get_settings
from callendulla.db.models import User, UserRole

_FERNET_KEY = Fernet.generate_key().decode()
_SECRET_32 = "a" * 32


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, str]]:
    data = {
        "TELEGRAM_BOT_TOKEN": "1234567890:AAFAKEFAKEFAKEFAKEFAKEFAKE00000000",
        "OWNER_TG_ID": "42",
        "LLM_PROVIDER": "gemini",
        "LLM_API_KEY": "AIzaSyFAKEFAKEFAKEFAKEFAKEFAKEFAKE000",
        "SECRET_KEY": _SECRET_32,
        "DIARY_ENCRYPTION_KEY": _FERNET_KEY,
        "AGPL_SOURCE_URL": "https://github.com/jetmil/callendulla",
    }
    for k in data:
        monkeypatch.delenv(k, raising=False)
    for k, v in data.items():
        monkeypatch.setenv(k, v)
    monkeypatch.chdir("/tmp")
    get_settings.cache_clear()
    yield data
    get_settings.cache_clear()


def _mock_message() -> MagicMock:
    message = MagicMock()
    message.answer = AsyncMock()
    return message


def _owner_user() -> User:
    return User(
        tg_id=42,
        tg_username="owner_alice",
        role=UserRole.OWNER,
        ical_token="tok_owner",
    )


def _member_user() -> User:
    return User(
        tg_id=100,
        tg_username="bob",
        role=UserRole.MEMBER,
        ical_token="tok_bob",
    )


class TestStart:
    async def test_owner_gets_owner_welcome(self) -> None:
        message = _mock_message()
        await handle_start(message, _owner_user())
        message.answer.assert_awaited_once()
        text: str = message.answer.await_args.args[0]
        assert "owner" in text.lower()

    async def test_member_gets_member_welcome(self) -> None:
        message = _mock_message()
        await handle_start(message, _member_user())
        message.answer.assert_awaited_once()
        text: str = message.answer.await_args.args[0]
        assert "member" in text.lower()
        assert "owner" not in text.lower().split("member")[0]  # member badge, not owner

    async def test_unregistered_gets_refusal(self) -> None:
        message = _mock_message()
        await handle_start(message, None)
        message.answer.assert_awaited_once()
        text: str = message.answer.await_args.args[0]
        assert "не принимает" in text.lower() or "не принимает" in text


class TestSource:
    async def test_returns_agpl_disclosure(self, env: dict[str, str]) -> None:
        message = _mock_message()
        settings = Settings()  # type: ignore[call-arg]
        await handle_source(message, settings)
        message.answer.assert_awaited_once()
        text: str = message.answer.await_args.args[0]
        assert "AGPL-3.0" in text
        assert "github.com/jetmil/callendulla" in text
        # commit_sha is either 40-char hex or "unknown" — both fine
        assert "Commit" in text

    async def test_respects_agpl_source_url_override(
        self, env: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AGPL_SOURCE_URL", "https://gitlab.com/me/my-fork")
        get_settings.cache_clear()
        settings = Settings()  # type: ignore[call-arg]
        message = _mock_message()
        await handle_source(message, settings)
        message.answer.assert_awaited_once()
        text: str = message.answer.await_args.args[0]
        assert "gitlab.com/me/my-fork" in text


class TestHelp:
    async def test_returns_command_list(self) -> None:
        message = _mock_message()
        await handle_help(message)
        message.answer.assert_awaited_once()
        text: str = message.answer.await_args.args[0]
        assert "/start" in text
        assert "/source" in text
        assert "/help" in text

    async def test_disable_web_preview_set(self) -> None:
        """``/help`` text contains a GitHub URL — we don't want the link
        preview thumbnail eating screen space in chat."""
        message = _mock_message()
        await handle_help(message)
        message.answer.assert_awaited_once()
        kwargs = message.answer.await_args.kwargs
        assert kwargs.get("disable_web_page_preview") is True
