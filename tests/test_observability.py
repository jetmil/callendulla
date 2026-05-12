# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Tests for :mod:`callendulla.core.observability`.

Verifies:
- No DSN → init returns False, sentry_sdk.init is NEVER called
- DSN set → sentry_sdk.init is called once with the operator's DSN
  and a release tag
- ``before_send`` callback redacts secret-shaped strings from
  outgoing events
- ``send_default_pii=False`` — user-typed content never goes to
  Sentry by default
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from callendulla.core.observability import _strip_secrets, init_observability


def _settings_stub(dsn: str | None) -> MagicMock:
    s = MagicMock()
    s.sentry_dsn = SecretStr(dsn) if dsn else None
    return s


@pytest.fixture
def _stub_sentry_sdk():
    """Replace sentry_sdk + all integrations with mocks."""
    fake_sentry = MagicMock()
    fake_fastapi = MagicMock()
    fake_loguru = MagicMock()
    fake_sqlalchemy = MagicMock()
    fake_starlette = MagicMock()
    modules = {
        "sentry_sdk": fake_sentry,
        "sentry_sdk.integrations": MagicMock(),
        "sentry_sdk.integrations.fastapi": fake_fastapi,
        "sentry_sdk.integrations.loguru": fake_loguru,
        "sentry_sdk.integrations.sqlalchemy": fake_sqlalchemy,
        "sentry_sdk.integrations.starlette": fake_starlette,
    }
    with patch.dict("sys.modules", modules):
        yield {
            "sentry_sdk": fake_sentry,
            "fastapi": fake_fastapi,
            "loguru": fake_loguru,
            "sqlalchemy": fake_sqlalchemy,
            "starlette": fake_starlette,
        }


class TestNoDSN:
    def test_returns_false_when_dsn_unset(self, _stub_sentry_sdk: dict) -> None:
        settings = _settings_stub(None)
        assert init_observability(settings) is False
        _stub_sentry_sdk["sentry_sdk"].init.assert_not_called()

    def test_returns_false_when_dsn_empty_string(self, _stub_sentry_sdk: dict) -> None:
        settings = _settings_stub("   ")
        assert init_observability(settings) is False
        _stub_sentry_sdk["sentry_sdk"].init.assert_not_called()


class TestWithDSN:
    def test_calls_sentry_init_with_dsn(self, _stub_sentry_sdk: dict) -> None:
        settings = _settings_stub("https://abc@sentry.io/12345")
        assert init_observability(settings) is True

        sentry = _stub_sentry_sdk["sentry_sdk"]
        sentry.init.assert_called_once()
        kwargs = sentry.init.call_args.kwargs
        assert kwargs["dsn"] == "https://abc@sentry.io/12345"
        # PII off by default
        assert kwargs["send_default_pii"] is False
        # Release tagged with our package version
        assert kwargs["release"].startswith("callendulla@")
        # A scrubber is installed
        assert callable(kwargs["before_send"])

    def test_strips_dsn_whitespace(self, _stub_sentry_sdk: dict) -> None:
        settings = _settings_stub("  https://abc@sentry.io/12345\n")
        init_observability(settings)
        kwargs = _stub_sentry_sdk["sentry_sdk"].init.call_args.kwargs
        assert kwargs["dsn"] == "https://abc@sentry.io/12345"

    def test_idempotent(self, _stub_sentry_sdk: dict) -> None:
        """Calling twice still only sends one wire init per call —
        sentry's own state is process-global and last-wins."""
        settings = _settings_stub("https://abc@sentry.io/12345")
        init_observability(settings)
        init_observability(settings)
        assert _stub_sentry_sdk["sentry_sdk"].init.call_count == 2


class TestBeforeSendScrubber:
    """The before_send callback runs ``redact()`` over every string
    in the outbound Sentry payload. Token-shaped strings must NOT
    leak to Sentry under any circumstances."""

    def test_scrubs_telegram_token_in_message(self) -> None:
        event = {
            "message": ("request failed: token=1234567890:AAFAKEFAKEFAKEFAKEFAKEFAKE000000000")
        }
        out = _strip_secrets(event, {})
        assert "1234567890:AAFAKEFAKEFAKE" not in str(out)

    def test_scrubs_anthropic_key_in_extra(self) -> None:
        event = {
            "extra": {
                "exc": "sk-ant-FAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKE",
            }
        }
        out = _strip_secrets(event, {})
        assert "FAKEFAKEFAKE" not in str(out)

    def test_scrubs_fernet_diary_key(self) -> None:
        key = Fernet.generate_key().decode()
        event = {"breadcrumbs": [{"data": {"diary_encryption_key": key}}]}
        out = _strip_secrets(event, {})
        # The key value is replaced with *** because it matches a
        # key=value pattern (key=...).  At minimum the raw key bytes
        # MUST NOT appear in the payload.
        assert key not in str(out)

    def test_non_string_values_pass_through(self) -> None:
        event = {
            "level": "error",
            "tags": ["http", "rate_limit"],
            "request_id": 12345,
            "nested": {"timing_ms": 230, "tags": ["bot"]},
        }
        out = _strip_secrets(event, {})
        assert out["level"] == "error"
        assert out["request_id"] == 12345
        assert out["nested"]["timing_ms"] == 230
        assert out["nested"]["tags"] == ["bot"]
