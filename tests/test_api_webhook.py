# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Tests for the Telegram webhook receiver.

Webhook-specific concerns:

- ``X-Telegram-Bot-Api-Secret-Token`` header must be required and
  constant-time-compared.
- Wrong / missing header → 403, body never reaches the dispatcher.
- Polling-mode app does NOT mount the webhook router — its path 404s.
- Malformed JSON body → 400 without leaking the parser exception.
- Successful dispatch returns 200, even when the handler raises (we
  don't want Telegram to retry).

We stub the aiogram Bot session out completely — the test must not hit
the Telegram API just because the lifespan tries to ``set_webhook``.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from callendulla.api import create_app
from callendulla.api.webhook import WEBHOOK_SECRET_HEADER
from callendulla.config import get_settings

_FERNET_KEY = Fernet.generate_key().decode()
_SECRET_32 = "a" * 32
_WEBHOOK_SECRET = "b" * 32
_WEBHOOK_PATH = "/tg/random-obfuscation-path"


def _webhook_env() -> dict[str, str]:
    return {
        "TELEGRAM_BOT_TOKEN": "1234567890:AAFAKEFAKEFAKEFAKEFAKEFAKE00000000",
        "OWNER_TG_ID": "42",
        "LLM_PROVIDER": "gemini",
        "LLM_API_KEY": "AIzaSyFAKEFAKEFAKEFAKEFAKEFAKEFAKE000",
        "SECRET_KEY": _SECRET_32,
        "DIARY_ENCRYPTION_KEY": _FERNET_KEY,
        "BOT_MODE": "webhook",
        "WEBHOOK_HOST": "https://callendulla.example.com",
        "WEBHOOK_PATH": _WEBHOOK_PATH,
        "WEBHOOK_SECRET": _WEBHOOK_SECRET,
        "ALLOWED_HOSTS": "*",
    }


def _polling_env() -> dict[str, str]:
    return {
        "TELEGRAM_BOT_TOKEN": "1234567890:AAFAKEFAKEFAKEFAKEFAKEFAKE00000000",
        "OWNER_TG_ID": "42",
        "LLM_PROVIDER": "gemini",
        "LLM_API_KEY": "AIzaSyFAKEFAKEFAKEFAKEFAKEFAKEFAKE000",
        "SECRET_KEY": _SECRET_32,
        "DIARY_ENCRYPTION_KEY": _FERNET_KEY,
        "BOT_MODE": "polling",
        "ALLOWED_HOSTS": "*",
    }


@pytest.fixture
def webhook_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for k in (*_webhook_env(), "AGPL_SOURCE_URL", "CORS_ORIGINS"):
        monkeypatch.delenv(k, raising=False)
    for k, v in _webhook_env().items():
        monkeypatch.setenv(k, v)
    monkeypatch.chdir("/tmp")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def polling_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for k in (
        *_polling_env(),
        "WEBHOOK_HOST",
        "WEBHOOK_PATH",
        "WEBHOOK_SECRET",
        "AGPL_SOURCE_URL",
        "CORS_ORIGINS",
    ):
        monkeypatch.delenv(k, raising=False)
    for k, v in _polling_env().items():
        monkeypatch.setenv(k, v)
    monkeypatch.chdir("/tmp")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def stub_bot() -> Iterator[MagicMock]:
    """Replace :func:`create_bot` so we don't open a real aiohttp session.

    The lifespan calls ``set_webhook`` / ``delete_webhook`` / ``session.close``
    — all are AsyncMocks so they record what was attempted.
    """
    bot = MagicMock()
    bot.set_webhook = AsyncMock()
    bot.delete_webhook = AsyncMock()
    bot.session.close = AsyncMock()
    with patch("callendulla.api.app.create_bot", return_value=bot):
        yield bot


@pytest.fixture
def stub_dispatcher() -> Iterator[MagicMock]:
    dp = MagicMock()
    # Aiogram's resolve_used_update_types returns a list of strings;
    # the mock can return whatever — set_webhook just forwards it.
    dp.resolve_used_update_types.return_value = ["message", "callback_query"]
    dp.feed_webhook_update = AsyncMock()
    with patch("callendulla.api.app.create_dispatcher", return_value=dp):
        yield dp


def _valid_update_payload() -> dict[str, Any]:
    return {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "date": 0,
            "chat": {"id": 42, "type": "private"},
            "from": {"id": 42, "is_bot": False, "first_name": "Test"},
            "text": "/start",
        },
    }


class TestWebhookMountedOnlyInWebhookMode:
    def test_polling_mode_does_not_mount(self, polling_env: None) -> None:
        with TestClient(create_app()) as c:
            # In polling mode no webhook path is defined; any POST gets 404.
            r = c.post("/tg/anything", json=_valid_update_payload())
        assert r.status_code == 404

    def test_webhook_mode_mounts(
        self,
        webhook_env: None,
        stub_bot: MagicMock,
        stub_dispatcher: MagicMock,
    ) -> None:
        with TestClient(create_app()) as c:
            r = c.post(
                _WEBHOOK_PATH,
                json=_valid_update_payload(),
                headers={WEBHOOK_SECRET_HEADER: _WEBHOOK_SECRET},
            )
        assert r.status_code == 200
        # Bot was asked to set the webhook on startup
        stub_bot.set_webhook.assert_awaited_once()
        # And dropped on shutdown
        stub_bot.delete_webhook.assert_awaited_once()


class TestSecretHeaderEnforcement:
    def test_missing_header_rejected(
        self,
        webhook_env: None,
        stub_bot: MagicMock,
        stub_dispatcher: MagicMock,
    ) -> None:
        with TestClient(create_app()) as c:
            r = c.post(_WEBHOOK_PATH, json=_valid_update_payload())
        assert r.status_code == 403
        # Body never reached the dispatcher
        stub_dispatcher.feed_webhook_update.assert_not_awaited()

    def test_wrong_secret_rejected(
        self,
        webhook_env: None,
        stub_bot: MagicMock,
        stub_dispatcher: MagicMock,
    ) -> None:
        with TestClient(create_app()) as c:
            r = c.post(
                _WEBHOOK_PATH,
                json=_valid_update_payload(),
                headers={WEBHOOK_SECRET_HEADER: "wrong-secret-value"},
            )
        assert r.status_code == 403
        stub_dispatcher.feed_webhook_update.assert_not_awaited()

    def test_correct_secret_accepted(
        self,
        webhook_env: None,
        stub_bot: MagicMock,
        stub_dispatcher: MagicMock,
    ) -> None:
        with TestClient(create_app()) as c:
            r = c.post(
                _WEBHOOK_PATH,
                json=_valid_update_payload(),
                headers={WEBHOOK_SECRET_HEADER: _WEBHOOK_SECRET},
            )
        assert r.status_code == 200
        stub_dispatcher.feed_webhook_update.assert_awaited_once()


class TestUpdatePayloadValidation:
    def test_malformed_json_returns_400(
        self,
        webhook_env: None,
        stub_bot: MagicMock,
        stub_dispatcher: MagicMock,
    ) -> None:
        with TestClient(create_app()) as c:
            r = c.post(
                _WEBHOOK_PATH,
                json={"update_id": "not-an-int"},
                headers={WEBHOOK_SECRET_HEADER: _WEBHOOK_SECRET},
            )
        assert r.status_code == 400
        stub_dispatcher.feed_webhook_update.assert_not_awaited()


class TestHandlerExceptionsSwallowed:
    def test_dispatcher_error_returns_200(
        self,
        webhook_env: None,
        stub_bot: MagicMock,
        stub_dispatcher: MagicMock,
    ) -> None:
        """If a handler raises, we still return 200 — Telegram would
        retry on non-2xx for up to 60 minutes and that would amplify
        bugs into a queue flood. The exception lives in the logs."""
        stub_dispatcher.feed_webhook_update.side_effect = RuntimeError("boom")
        with TestClient(create_app()) as c:
            r = c.post(
                _WEBHOOK_PATH,
                json=_valid_update_payload(),
                headers={WEBHOOK_SECRET_HEADER: _WEBHOOK_SECRET},
            )
        assert r.status_code == 200
